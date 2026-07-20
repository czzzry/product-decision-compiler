"""Read-only Linear and GitHub adapters for the Product Decision Compiler.

The adapters deliberately stop at fetching and normalising external records. They
never create, update, comment on, label, or otherwise mutate provider state.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import Field

from .contracts import DecisionPackage, DeliveryReport, StrictModel, WorkItemEvidence


class ReadOnlyIntegrationError(RuntimeError):
    """Raised when a provider cannot return a valid read-only response."""


class JSONTransport(Protocol):
    """Small seam that keeps provider clients deterministic and easy to test."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: bytes | None = None,
    ) -> object: ...


class UrllibJSONTransport:
    """Standard-library JSON transport used by the real adapters."""

    def __init__(self, *, timeout_seconds: int = 20) -> None:
        self._timeout_seconds = timeout_seconds

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: bytes | None = None,
    ) -> object:
        request = urllib.request.Request(
            url,
            data=payload,
            headers=dict(headers),
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ReadOnlyIntegrationError(
                f"Read-only provider request failed with HTTP {error.code}."
            ) from error
        except urllib.error.URLError as error:
            raise ReadOnlyIntegrationError(
                "Read-only provider endpoint was unreachable."
            ) from error
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReadOnlyIntegrationError("Provider returned invalid JSON.") from error


class DecisionReference(StrictModel):
    """Decision version explicitly named by an external work item."""

    decision_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    decision_version: int = Field(ge=1)

    @property
    def version_id(self) -> str:
        return f"{self.decision_id}-v{self.decision_version}"


class DecisionBinding(StrictModel):
    """Explicit link used to decide which external records belong to a decision."""

    decision_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]+$")
    decision_version: int = Field(ge=1)

    @property
    def version_id(self) -> str:
        return f"{self.decision_id}-v{self.decision_version}"

    @property
    def marker(self) -> str:
        return f"decision:{self.version_id}"

    @classmethod
    def from_package(cls, package: DecisionPackage) -> DecisionBinding:
        return cls(decision_id=package.decision_id, decision_version=package.version)

    def reference_for(self, text: str) -> DecisionReference | None:
        reference = extract_decision_reference(text)
        if reference is None or reference.decision_id != self.decision_id:
            return None
        return reference


class IntegrationEvidenceBatch(StrictModel):
    """Normalised provider records ready for the deterministic conformance engine."""

    provider: Literal["linear", "github"]
    decision_id: str
    work_items: list[WorkItemEvidence] = Field(default_factory=list)
    delivery_reports: list[DeliveryReport] = Field(default_factory=list)
    unmatched_records: int = Field(ge=0)


class LinearIssueRecord(StrictModel):
    id: str
    identifier: str
    title: str
    description: str = ""
    url: str = ""
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    project_name: str | None = None
    parent_identifier: str | None = None
    labels: list[str] = Field(default_factory=list)


class GitHubIssueRecord(StrictModel):
    number: int = Field(ge=1)
    title: str
    body: str = ""
    html_url: str = ""
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    is_pull_request: bool = False


class GitHubPullRequestRecord(StrictModel):
    number: int = Field(ge=1)
    title: str
    body: str = ""
    html_url: str = ""
    created_at_ms: int = Field(ge=0)
    updated_at_ms: int = Field(ge=0)
    head_sha: str = ""


class GitHubCommitRecord(StrictModel):
    sha: str = Field(min_length=7)
    message: str
    html_url: str = ""
    created_at_ms: int = Field(ge=0)
    pull_request_number: int | None = Field(default=None, ge=1)


class GitHubFileRecord(StrictModel):
    filename: str = Field(min_length=1)
    status: str = "modified"
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)


class GitHubCheckRunRecord(StrictModel):
    name: str = Field(min_length=1)
    status: str = "completed"
    conclusion: str | None = None


class LinearReadOnlyAdapter:
    """Read Linear issues and map explicitly linked records to compiler evidence."""

    _QUERY = """
    query DecisionCompilerIssues($teamId: String!, $first: Int!) {
      team(id: $teamId) {
        issues(first: $first) {
          nodes {
            id
            identifier
            title
            description
            url
            createdAt
            updatedAt
            project { name }
            parent { identifier }
            labels { nodes { name } }
          }
        }
      }
    }
    """

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = "https://api.linear.app/graphql",
        authorization_scheme: Literal["api_key", "bearer"] = "api_key",
        transport: JSONTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Linear API key must not be empty.")
        self._api_key = api_key
        self._endpoint = endpoint
        self._authorization_scheme = authorization_scheme
        self._transport = transport or UrllibJSONTransport()

    @classmethod
    def from_env(cls, *, transport: JSONTransport | None = None) -> LinearReadOnlyAdapter:
        api_key = os.environ.get("LINEAR_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("LINEAR_API_KEY is required for the read-only Linear adapter.")
        return cls(api_key, transport=transport)

    def list_issues(self, *, team_id: str, first: int = 50) -> list[LinearIssueRecord]:
        if not team_id.strip():
            raise ValueError("Linear team_id must not be empty.")
        if not 1 <= first <= 100:
            raise ValueError("Linear first must be between 1 and 100.")
        payload = self._transport.request(
            method="POST",
            url=self._endpoint,
            headers={
                "Authorization": (
                    self._api_key
                    if self._authorization_scheme == "api_key"
                    else f"Bearer {self._api_key}"
                ),
                "Content-Type": "application/json",
                "User-Agent": "product-decision-compiler/0.1",
            },
            payload=json.dumps(
                {"query": self._QUERY, "variables": {"teamId": team_id, "first": first}}
            ).encode("utf-8"),
        )
        root = _object(payload, "Linear response")
        if root.get("errors"):
            raise ReadOnlyIntegrationError("Linear returned a GraphQL error.")
        team = _object(root.get("data"), "Linear response data").get("team")
        if team is None:
            raise ReadOnlyIntegrationError("Linear team was not found or was not readable.")
        nodes = _object(_object(team, "Linear team").get("issues"), "Linear issues").get("nodes")
        if not isinstance(nodes, list):
            raise ReadOnlyIntegrationError("Linear returned an invalid issue collection.")
        return [_linear_issue(node) for node in nodes if isinstance(node, Mapping)]

    def collect_for_decision(
        self,
        package: DecisionPackage,
        *,
        team_id: str,
        first: int = 50,
    ) -> IntegrationEvidenceBatch:
        binding = DecisionBinding.from_package(package)
        work_items: list[WorkItemEvidence] = []
        unmatched = 0
        for issue in self.list_issues(team_id=team_id, first=first):
            reference = binding.reference_for(_linear_text(issue))
            if reference is None:
                unmatched += 1
                continue
            work_items.append(_linear_evidence(issue, reference))
        return IntegrationEvidenceBatch(
            provider="linear",
            decision_id=package.decision_id,
            work_items=work_items,
            unmatched_records=unmatched,
        )


class GitHubReadOnlyAdapter:
    """Read GitHub issues, pull requests, commits, files, and checks."""

    def __init__(
        self,
        token: str | None = None,
        *,
        api_base_url: str = "https://api.github.com",
        transport: JSONTransport | None = None,
    ) -> None:
        self._token = token.strip() if token else None
        self._api_base_url = api_base_url.rstrip("/")
        self._transport = transport or UrllibJSONTransport()

    @classmethod
    def from_env(cls, *, transport: JSONTransport | None = None) -> GitHubReadOnlyAdapter:
        return cls(os.environ.get("GITHUB_TOKEN"), transport=transport)

    def list_issues(
        self,
        *,
        owner: str,
        repo: str,
        state: Literal["open", "closed", "all"] = "all",
        per_page: int = 100,
    ) -> list[GitHubIssueRecord]:
        payload = self._get(
            f"/repos/{_segment(owner)}/{_segment(repo)}/issues",
            state=state,
            per_page=_page_size(per_page),
        )
        if not isinstance(payload, list):
            raise ReadOnlyIntegrationError("GitHub returned an invalid issue collection.")
        return [_github_issue(item) for item in payload if isinstance(item, Mapping)]

    def list_pull_requests(
        self,
        *,
        owner: str,
        repo: str,
        state: Literal["open", "closed", "all"] = "all",
        per_page: int = 100,
    ) -> list[GitHubPullRequestRecord]:
        payload = self._get(
            f"/repos/{_segment(owner)}/{_segment(repo)}/pulls",
            state=state,
            per_page=_page_size(per_page),
        )
        if not isinstance(payload, list):
            raise ReadOnlyIntegrationError("GitHub returned an invalid pull-request collection.")
        return [_github_pull_request(item) for item in payload if isinstance(item, Mapping)]

    def list_commits(
        self,
        *,
        owner: str,
        repo: str,
        per_page: int = 100,
    ) -> list[GitHubCommitRecord]:
        payload = self._get(
            f"/repos/{_segment(owner)}/{_segment(repo)}/commits",
            per_page=_page_size(per_page),
        )
        if not isinstance(payload, list):
            raise ReadOnlyIntegrationError("GitHub returned an invalid commit collection.")
        return [_github_commit(item) for item in payload if isinstance(item, Mapping)]

    def list_pull_request_files(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        per_page: int = 100,
    ) -> list[GitHubFileRecord]:
        payload = self._get(
            f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{number}/files",
            per_page=_page_size(per_page),
        )
        if not isinstance(payload, list):
            raise ReadOnlyIntegrationError("GitHub returned an invalid changed-file collection.")
        return [_github_file(item) for item in payload if isinstance(item, Mapping)]

    def list_pull_request_commits(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        per_page: int = 100,
    ) -> list[GitHubCommitRecord]:
        payload = self._get(
            f"/repos/{_segment(owner)}/{_segment(repo)}/pulls/{number}/commits",
            per_page=_page_size(per_page),
        )
        if not isinstance(payload, list):
            raise ReadOnlyIntegrationError("GitHub returned an invalid pull-request commit list.")
        return [
            _github_commit(item, pull_request_number=number)
            for item in payload
            if isinstance(item, Mapping)
        ]

    def list_check_runs(
        self,
        *,
        owner: str,
        repo: str,
        ref: str,
    ) -> list[GitHubCheckRunRecord]:
        payload = self._get(
            f"/repos/{_segment(owner)}/{_segment(repo)}/commits/{_segment(ref)}/check-runs"
        )
        checks = _object(payload, "GitHub check-run response").get("check_runs")
        if not isinstance(checks, list):
            raise ReadOnlyIntegrationError("GitHub returned an invalid check-run collection.")
        return [_github_check_run(item) for item in checks if isinstance(item, Mapping)]

    def collect_for_decision(
        self,
        package: DecisionPackage,
        *,
        owner: str,
        repo: str,
        per_page: int = 100,
    ) -> IntegrationEvidenceBatch:
        binding = DecisionBinding.from_package(package)
        work_items: list[WorkItemEvidence] = []
        delivery_reports: list[DeliveryReport] = []
        unmatched = 0
        matched_prs: dict[int, DecisionReference] = {}
        unlinked_commits: list[GitHubCommitRecord] = []

        for issue in self.list_issues(owner=owner, repo=repo, per_page=per_page):
            if issue.is_pull_request:
                continue
            reference = binding.reference_for(_github_issue_text(issue))
            if reference is None:
                unmatched += 1
                continue
            work_items.append(_github_issue_evidence(owner, repo, issue, reference))

        for pull_request in self.list_pull_requests(owner=owner, repo=repo, per_page=per_page):
            reference = binding.reference_for(_github_pull_request_text(pull_request))
            if reference is None:
                unmatched += 1
                continue
            matched_prs[pull_request.number] = reference
            work_items.append(_github_pull_request_evidence(owner, repo, pull_request, reference))
            files = self.list_pull_request_files(
                owner=owner,
                repo=repo,
                number=pull_request.number,
                per_page=per_page,
            )
            checks = (
                self.list_check_runs(
                    owner=owner,
                    repo=repo,
                    ref=pull_request.head_sha,
                )
                if pull_request.head_sha
                else []
            )
            delivery_reports.append(
                _github_delivery_report(owner, repo, pull_request, files, checks, reference)
            )

        seen_commits: set[str] = set()
        for commit in self.list_commits(owner=owner, repo=repo, per_page=per_page):
            reference = binding.reference_for(commit.message)
            if reference is None:
                unlinked_commits.append(commit)
                continue
            seen_commits.add(commit.sha)
            work_items.append(_github_commit_evidence(owner, repo, commit, reference))

        for number, reference in matched_prs.items():
            for commit in self.list_pull_request_commits(
                owner=owner,
                repo=repo,
                number=number,
                per_page=per_page,
            ):
                if commit.sha in seen_commits:
                    continue
                seen_commits.add(commit.sha)
                commit_reference = binding.reference_for(commit.message) or reference
                work_items.append(_github_commit_evidence(owner, repo, commit, commit_reference))

        unmatched += sum(commit.sha not in seen_commits for commit in unlinked_commits)

        return IntegrationEvidenceBatch(
            provider="github",
            decision_id=package.decision_id,
            work_items=work_items,
            delivery_reports=delivery_reports,
            unmatched_records=unmatched,
        )

    def _get(self, path: str, **params: object) -> object:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value})
        url = f"{self._api_base_url}{path}"
        if query:
            url = f"{url}?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "product-decision-compiler/0.1",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return self._transport.request(method="GET", url=url, headers=headers)


def extract_decision_reference(text: str) -> DecisionReference | None:
    """Read an explicit ``decision:<id>-v<version>`` marker from provider text."""

    match = re.search(
        r"(?<![a-z0-9])(?:decision|pdc)\s*:\s*([a-z0-9][a-z0-9_-]*)-v([1-9][0-9]*)\b",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return DecisionReference(
        decision_id=match.group(1).lower(),
        decision_version=int(match.group(2)),
    )


def _linear_issue(payload: Mapping[str, object]) -> LinearIssueRecord:
    project = payload.get("project")
    parent = payload.get("parent")
    labels = _object(payload.get("labels") or {"nodes": []}, "Linear labels").get("nodes")
    return LinearIssueRecord(
        id=str(payload.get("id") or ""),
        identifier=str(payload.get("identifier") or ""),
        title=str(payload.get("title") or ""),
        description=str(payload.get("description") or ""),
        url=str(payload.get("url") or ""),
        created_at_ms=_timestamp(payload.get("createdAt")),
        updated_at_ms=_timestamp(payload.get("updatedAt")),
        project_name=(
            str(_object(project, "Linear project").get("name"))
            if project
            else None
        ),
        parent_identifier=(
            str(_object(parent, "Linear parent").get("identifier"))
            if parent
            else None
        ),
        labels=[str(_object(label, "Linear label").get("name")) for label in labels or []],
    )


def _github_issue(payload: Mapping[str, object]) -> GitHubIssueRecord:
    return GitHubIssueRecord(
        number=int(payload.get("number") or 0),
        title=str(payload.get("title") or ""),
        body=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or ""),
        created_at_ms=_timestamp(payload.get("created_at")),
        updated_at_ms=_timestamp(payload.get("updated_at")),
        is_pull_request=isinstance(payload.get("pull_request"), Mapping),
    )


def _github_pull_request(payload: Mapping[str, object]) -> GitHubPullRequestRecord:
    head = payload.get("head")
    return GitHubPullRequestRecord(
        number=int(payload.get("number") or 0),
        title=str(payload.get("title") or ""),
        body=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or ""),
        created_at_ms=_timestamp(payload.get("created_at")),
        updated_at_ms=_timestamp(payload.get("updated_at")),
        head_sha=str(_object(head, "GitHub pull-request head").get("sha") or "") if head else "",
    )


def _github_commit(
    payload: Mapping[str, object],
    *,
    pull_request_number: int | None = None,
) -> GitHubCommitRecord:
    commit = _object(payload.get("commit"), "GitHub commit")
    author = commit.get("author")
    committer = commit.get("committer")
    date = _object(committer, "GitHub committer").get("date") if committer else None
    if not date and author:
        date = _object(author, "GitHub author").get("date")
    return GitHubCommitRecord(
        sha=str(payload.get("sha") or ""),
        message=str(commit.get("message") or payload.get("message") or ""),
        html_url=str(payload.get("html_url") or ""),
        created_at_ms=_timestamp(date),
        pull_request_number=pull_request_number,
    )


def _github_file(payload: Mapping[str, object]) -> GitHubFileRecord:
    return GitHubFileRecord(
        filename=str(payload.get("filename") or ""),
        status=str(payload.get("status") or "modified"),
        additions=int(payload.get("additions") or 0),
        deletions=int(payload.get("deletions") or 0),
    )


def _github_check_run(payload: Mapping[str, object]) -> GitHubCheckRunRecord:
    return GitHubCheckRunRecord(
        name=str(payload.get("name") or "Unnamed check"),
        status=str(payload.get("status") or "unknown"),
        conclusion=str(payload.get("conclusion")) if payload.get("conclusion") else None,
    )


def _linear_text(issue: LinearIssueRecord) -> str:
    metadata = [
        f"Project: {issue.project_name}" if issue.project_name else "",
        f"Parent: {issue.parent_identifier}" if issue.parent_identifier else "",
        f"Labels: {', '.join(issue.labels)}" if issue.labels else "",
    ]
    return "\n".join([issue.title, issue.description, *metadata])


def _github_issue_text(issue: GitHubIssueRecord) -> str:
    return f"{issue.title}\n{issue.body}\n{issue.html_url}"


def _github_pull_request_text(pull_request: GitHubPullRequestRecord) -> str:
    return f"{pull_request.title}\n{pull_request.body}\n{pull_request.html_url}"


def _linear_evidence(issue: LinearIssueRecord, reference: DecisionReference) -> WorkItemEvidence:
    description = _with_metadata(
        issue.description,
        [
            f"Linear issue: {issue.identifier}",
            f"Project: {issue.project_name}" if issue.project_name else "",
            f"Parent: {issue.parent_identifier}" if issue.parent_identifier else "",
            f"Labels: {', '.join(issue.labels)}" if issue.labels else "",
            f"URL: {issue.url}" if issue.url else "",
        ],
    )
    return WorkItemEvidence(
        source_type="sub_issue" if issue.parent_identifier else "issue",
        source_id=issue.identifier or issue.id,
        source_event_id=f"linear:issue:{issue.id}:{issue.updated_at_ms}",
        decision_id=reference.decision_id,
        decision_version=reference.decision_version,
        title=issue.title,
        description=description,
        acceptance_criteria_refs=_tagged_lines(issue.description, "acceptance"),
        created_at_ms=issue.created_at_ms,
    )


def _github_issue_evidence(
    owner: str,
    repo: str,
    issue: GitHubIssueRecord,
    reference: DecisionReference,
) -> WorkItemEvidence:
    return WorkItemEvidence(
        source_type="issue",
        source_id=f"{owner}/{repo}#{issue.number}",
        source_event_id=f"github:issue:{owner}/{repo}:{issue.number}:{issue.updated_at_ms}",
        decision_id=reference.decision_id,
        decision_version=reference.decision_version,
        title=issue.title,
        description=_with_metadata(
            issue.body,
            [f"URL: {issue.html_url}" if issue.html_url else ""],
        ),
        acceptance_criteria_refs=_tagged_lines(issue.body, "acceptance"),
        created_at_ms=issue.created_at_ms,
    )


def _github_pull_request_evidence(
    owner: str,
    repo: str,
    pull_request: GitHubPullRequestRecord,
    reference: DecisionReference,
) -> WorkItemEvidence:
    return WorkItemEvidence(
        source_type="pull_request",
        source_id=f"{owner}/{repo}#pr-{pull_request.number}",
        source_event_id=(
            f"github:pull_request:{owner}/{repo}:{pull_request.number}:"
            f"{pull_request.updated_at_ms}"
        ),
        decision_id=reference.decision_id,
        decision_version=reference.decision_version,
        title=pull_request.title,
        description=_with_metadata(
            pull_request.body,
            [f"URL: {pull_request.html_url}" if pull_request.html_url else ""],
        ),
        acceptance_criteria_refs=_tagged_lines(pull_request.body, "acceptance"),
        created_at_ms=pull_request.created_at_ms,
    )


def _github_commit_evidence(
    owner: str,
    repo: str,
    commit: GitHubCommitRecord,
    reference: DecisionReference,
) -> WorkItemEvidence:
    linked = (
        f"Linked pull request: #{commit.pull_request_number}"
        if commit.pull_request_number
        else ""
    )
    return WorkItemEvidence(
        source_type="commit",
        source_id=f"{owner}/{repo}@{commit.sha}",
        source_event_id=f"github:commit:{owner}/{repo}:{commit.sha}",
        decision_id=reference.decision_id,
        decision_version=reference.decision_version,
        title=commit.message.splitlines()[0] or commit.sha,
        description=_with_metadata(commit.message, [linked, f"URL: {commit.html_url}"]),
        created_at_ms=commit.created_at_ms,
    )


def _github_delivery_report(
    owner: str,
    repo: str,
    pull_request: GitHubPullRequestRecord,
    files: Sequence[GitHubFileRecord],
    checks: Sequence[GitHubCheckRunRecord],
    reference: DecisionReference,
) -> DeliveryReport:
    test_lines = _tagged_lines(pull_request.body, "test")
    test_lines.extend(
        f"{check.name}: {check.conclusion or check.status}" for check in checks
    )
    return DeliveryReport(
        source_id=f"{owner}/{repo}#pr-{pull_request.number}",
        source_event_id=(
            f"github:delivery:{owner}/{repo}:{pull_request.number}:"
            f"{pull_request.updated_at_ms}"
        ),
        decision_id=reference.decision_id,
        decision_version=reference.decision_version,
        changed_areas=[file.filename for file in files] or [
            f"Pull request #{pull_request.number} (changed-file list unavailable)"
        ],
        tests=test_lines,
        deviations=_tagged_lines(pull_request.body, "deviation"),
        residual_risks=_tagged_lines(pull_request.body, "risk"),
        created_at_ms=pull_request.updated_at_ms,
    )


def _tagged_lines(text: str, tag: str) -> list[str]:
    prefix = f"{tag.lower()}:"
    return [
        line.split(":", 1)[1].strip()
        for line in text.splitlines()
        if line.strip().lower().startswith(prefix) and line.split(":", 1)[1].strip()
    ]


def _with_metadata(text: str, metadata: Sequence[str]) -> str:
    values = [item for item in [text.strip(), *metadata] if item]
    return "\n".join(values)


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReadOnlyIntegrationError(f"{label} was not an object.")
    return dict(value)


def _timestamp(value: object) -> int:
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number >= 10**12 else number * 1000
    if not value:
        return 0
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as error:
        raise ReadOnlyIntegrationError("Provider returned an invalid timestamp.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return int(parsed.timestamp() * 1000)


def _segment(value: str) -> str:
    if not value or value.strip() != value or "/" in value:
        raise ValueError("GitHub owner, repository, and reference must be safe path segments.")
    return urllib.parse.quote(value, safe="._-")


def _page_size(value: int) -> int:
    if not 1 <= value <= 100:
        raise ValueError("GitHub per_page must be between 1 and 100.")
    return value
