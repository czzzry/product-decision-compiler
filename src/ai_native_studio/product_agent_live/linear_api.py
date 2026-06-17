"""OAuth and GraphQL clients for the live Linear ProductAgent service."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import LiveProductAgentConfig
from .models import StoredInstallation, TokenResponse


class LinearAPIError(RuntimeError):
    """Base error for live Linear API calls."""


class LinearAuthError(LinearAPIError):
    """Raised for invalid or expired authentication."""


class LinearOAuthClient:
    def __init__(self, config: LiveProductAgentConfig, timeout_seconds: int = 30) -> None:
        self._config = config
        self._timeout_seconds = timeout_seconds

    def exchange_code(self, code: str) -> StoredInstallation:
        response = self._token_request(
            {
                "code": code,
                "redirect_uri": self._config.callback_url,
                "client_id": self._config.oauth_client_id,
                "client_secret": self._config.oauth_client_secret,
                "grant_type": "authorization_code",
            }
        )
        return self._stored_installation(response)

    def refresh(self, refresh_token: str) -> StoredInstallation:
        response = self._token_request(
            {
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            basic_auth=True,
        )
        return self._stored_installation(response)

    def _token_request(
        self,
        payload: dict[str, str],
        *,
        basic_auth: bool = False,
    ) -> TokenResponse:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if basic_auth:
            credentials = (
                f"{self._config.oauth_client_id}:{self._config.oauth_client_secret}".encode()
            )
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode("ascii")
        request = urllib.request.Request(
            self._config.linear_token_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            if error.code in {400, 401}:
                raise LinearAuthError("Linear OAuth request was rejected.") from error
            raise LinearAPIError("Linear OAuth request failed.") from error
        except urllib.error.URLError as error:
            raise LinearAPIError("Linear OAuth endpoint was unreachable.") from error
        return TokenResponse.model_validate_json(raw)

    @staticmethod
    def _stored_installation(response: TokenResponse) -> StoredInstallation:
        if not response.refresh_token:
            raise LinearAuthError("Linear did not return a refresh token for the installation.")
        scope = response.scope if isinstance(response.scope, str) else " ".join(response.scope)
        return StoredInstallation(
            access_token=response.access_token,
            refresh_token=response.refresh_token,
            expires_at_ms=int(time.time() * 1000) + response.expires_in * 1000,
            scope=tuple(scope.split()),
        )


class LinearGraphQLClient:
    def __init__(
        self,
        config: LiveProductAgentConfig,
        access_token: str,
        timeout_seconds: int = 30,
    ) -> None:
        self._config = config
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds

    def create_agent_activity(
        self,
        session_id: str,
        content: dict[str, object],
        *,
        ephemeral: bool = False,
    ) -> None:
        self.request(
            """
            mutation AgentActivityCreate($input: AgentActivityCreateInput!) {
              agentActivityCreate(input: $input) {
                success
              }
            }
            """,
            {
                "input": {
                    "agentSessionId": session_id,
                    "content": content,
                    "ephemeral": ephemeral,
                }
            },
        )

    def update_agent_session_external_urls(
        self,
        session_id: str,
        label: str,
        url: str,
    ) -> None:
        self.request(
            """
            mutation AgentSessionUpdate($id: String!, $input: AgentSessionUpdateInput!) {
              agentSessionUpdate(id: $id, input: $input) {
                success
              }
            }
            """,
            {
                "id": session_id,
                "input": {"externalUrls": [{"label": label, "url": url}]},
            },
        )

    def fetch_comment_author_id(self, comment_id: str) -> str | None:
        data = self.request(
            """
            query CommentAuthor($id: String!) {
              comment(id: $id) {
                id
                user {
                  id
                }
              }
            }
            """,
            {"id": comment_id},
        )
        comment = data.get("comment")
        if not isinstance(comment, dict):
            return None
        user = comment.get("user")
        if not isinstance(user, dict):
            return None
        user_id = user.get("id")
        return str(user_id) if user_id else None

    def fetch_issue_metadata(self, issue_id: str) -> dict[str, str | None]:
        data = self.request(
            """
            query IssueMetadata($id: String!) {
              organization {
                id
              }
              issue(id: $id) {
                id
                team {
                  id
                }
              }
            }
            """,
            {"id": issue_id},
        )
        organization = data.get("organization")
        issue = data.get("issue")
        team = issue.get("team") if isinstance(issue, dict) else None
        return {
            "workspace_id": (
                str(organization.get("id"))
                if isinstance(organization, dict) and organization.get("id")
                else None
            ),
            "team_id": str(team.get("id")) if isinstance(team, dict) and team.get("id") else None,
        }

    def fetch_issue_comments(self, issue_id: str) -> list[dict[str, object]]:
        data = self.request(
            """
            query IssueComments($id: String!) {
              issue(id: $id) {
                id
                comments {
                  nodes {
                    id
                    body
                    createdAt
                    user {
                      id
                    }
                  }
                }
              }
            }
            """,
            {"id": issue_id},
        )
        issue = data.get("issue")
        if not isinstance(issue, dict):
            return []
        comments = issue.get("comments")
        if not isinstance(comments, dict):
            return []
        nodes = comments.get("nodes")
        if not isinstance(nodes, list):
            return []
        return [node for node in nodes if isinstance(node, dict)]

    def fetch_agent_session_activities(self, session_id: str) -> list[dict[str, object]]:
        data = self.request(
            """
            query AgentSessionActivities($id: String!) {
              agentSession(id: $id) {
                id
                activities {
                  nodes {
                    id
                    body
                    type
                    createdAt
                    user {
                      id
                    }
                  }
                }
              }
            }
            """,
            {"id": session_id},
        )
        session = data.get("agentSession")
        if not isinstance(session, dict):
            return []
        activities = session.get("activities")
        if not isinstance(activities, dict):
            return []
        nodes = activities.get("nodes")
        if not isinstance(nodes, list):
            return []
        return [node for node in nodes if isinstance(node, dict)]

    def request(self, query: str, variables: dict[str, object]) -> dict[str, object]:
        payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        request = urllib.request.Request(
            self._config.linear_graphql_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 401:
                raise LinearAuthError("Linear access token is invalid or expired.") from error
            raise LinearAPIError(
                f"Linear GraphQL request failed with HTTP {error.code}."
            ) from error
        except urllib.error.URLError as error:
            raise LinearAPIError("Linear GraphQL endpoint was unreachable.") from error

        payload_json = json.loads(raw)
        if payload_json.get("errors"):
            first = payload_json["errors"][0]
            message = first.get("message", "Linear GraphQL returned an error.")
            extensions = first.get("extensions")
            code = None
            if isinstance(extensions, dict):
                code = extensions.get("code")
            if code:
                raise LinearAPIError(f"{code}: {message}")
            raise LinearAPIError(str(message))
        data = payload_json.get("data")
        if not isinstance(data, dict):
            raise LinearAPIError("Linear GraphQL returned no data payload.")
        return data
