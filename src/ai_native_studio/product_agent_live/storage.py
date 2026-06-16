"""Storage adapters for local proofing and durable Cloud Run state."""

from __future__ import annotations

from collections.abc import MutableMapping
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol

from ai_native_studio.product_agent_proof.approval import SyntheticApprovalRecord
from ai_native_studio.product_agent_proof.dedup import ReceiptResult, WebhookReceiptStore

from .config import LiveProductAgentConfig
from .models import StoredInstallation
from .product_briefs import (
    ProductBriefApprovalRecord,
    ProductBriefStoreProtocol,
    ProductBriefVersion,
)
from .tokens import InstallationStore, _normalize_fernet_key

if TYPE_CHECKING:
    from cryptography.fernet import Fernet


class OAuthStateStoreProtocol(Protocol):
    def create(self, state: str, created_at_ms: int | None = None) -> None: ...

    def pop(self, state: str, max_age_ms: int, now_ms: int | None = None) -> bool: ...


class InstallationStoreProtocol(Protocol):
    oauth_states: OAuthStateStoreProtocol

    def save_installation(self, installation: StoredInstallation) -> None: ...

    def load_installation(self) -> StoredInstallation | None: ...

    def set_metadata(self, key: str, value: str) -> None: ...

    def get_metadata(self, key: str) -> str | None: ...

    def close(self) -> None: ...


class ReceiptStoreProtocol(Protocol):
    def reserve(
        self, webhook_id: str, payload_sha256: str, received_at_ms: int
    ) -> ReceiptResult: ...

    def complete(self, webhook_id: str, payload_sha256: str) -> None: ...

    def release(self, webhook_id: str, payload_sha256: str) -> None: ...

    def close(self) -> None: ...


class ApprovalLedgerProtocol(Protocol):
    def append(self, record: SyntheticApprovalRecord) -> None: ...


class DocumentStoreProtocol(Protocol):
    def create_document(
        self,
        collection: str,
        document_id: str,
        data: dict[str, Any],
    ) -> bool: ...

    def get_document(self, collection: str, document_id: str) -> dict[str, Any] | None: ...

    def set_document(self, collection: str, document_id: str, data: dict[str, Any]) -> None: ...

    def delete_document(self, collection: str, document_id: str) -> None: ...

    def list_documents(self, collection: str) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class InMemoryProductBriefStore:
    def __init__(
        self,
        document_store: DocumentStoreProtocol | None = None,
        *,
        collection_prefix: str = "product_agent_live",
    ) -> None:
        self._document_store = document_store or InMemoryDocumentStore()
        self._versions_collection = f"{collection_prefix}_product_brief_versions"
        self._approvals_collection = f"{collection_prefix}_product_brief_approvals"

    def get_version(self, version_id: str) -> ProductBriefVersion | None:
        payload = self._document_store.get_document(self._versions_collection, version_id)
        return None if payload is None else ProductBriefVersion.model_validate(payload)

    def list_versions(self, brief_id: str) -> list[ProductBriefVersion]:
        payloads = self._document_store.list_documents(self._versions_collection)
        versions = [
            ProductBriefVersion.model_validate(payload)
            for payload in payloads
            if payload.get("brief_id") == brief_id
        ]
        return sorted(versions, key=lambda version: version.version)

    def create_version(self, brief: ProductBriefVersion) -> bool:
        return self._document_store.create_document(
            self._versions_collection,
            brief.version_id,
            brief.model_dump(),
        )

    def save_version(self, brief: ProductBriefVersion) -> None:
        self._document_store.set_document(
            self._versions_collection,
            brief.version_id,
            brief.model_dump(),
        )

    def create_approval(self, record: ProductBriefApprovalRecord) -> bool:
        return self._document_store.create_document(
            self._approvals_collection,
            record.approval_id,
            record.model_dump(),
        )

    def get_approval(self, approval_id: str) -> ProductBriefApprovalRecord | None:
        payload = self._document_store.get_document(self._approvals_collection, approval_id)
        return None if payload is None else ProductBriefApprovalRecord.model_validate(payload)

    def close(self) -> None:
        self._document_store.close()


class InMemoryDocumentStore:
    """Small shared document store used by persistence tests."""

    def __init__(
        self,
        backing: MutableMapping[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self._documents = backing if backing is not None else {}
        self._lock = Lock()

    def create_document(
        self,
        collection: str,
        document_id: str,
        data: dict[str, Any],
    ) -> bool:
        key = (collection, document_id)
        with self._lock:
            if key in self._documents:
                return False
            self._documents[key] = dict(data)
        return True

    def get_document(self, collection: str, document_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._documents.get((collection, document_id))
            return None if payload is None else dict(payload)

    def set_document(self, collection: str, document_id: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._documents[(collection, document_id)] = dict(data)

    def delete_document(self, collection: str, document_id: str) -> None:
        with self._lock:
            self._documents.pop((collection, document_id), None)

    def list_documents(self, collection: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(document)
                for (doc_collection, _), document in self._documents.items()
                if doc_collection == collection
            ]

    def close(self) -> None:
        return None


class FirestoreDocumentStore:
    """Minimal wrapper around Firestore document operations."""

    def __init__(
        self,
        *,
        project_id: str | None = None,
        database_id: str = "(default)",
    ) -> None:
        try:
            from google.api_core.exceptions import AlreadyExists
            from google.cloud import firestore
        except ImportError as error:
            raise RuntimeError(
                "google-cloud-firestore is required for PRODUCT_AGENT_STORAGE_BACKEND=firestore."
            ) from error

        self._client = firestore.Client(project=project_id, database=database_id)
        self._already_exists = AlreadyExists

    def create_document(
        self,
        collection: str,
        document_id: str,
        data: dict[str, Any],
    ) -> bool:
        try:
            self._client.collection(collection).document(document_id).create(dict(data))
        except self._already_exists:
            return False
        return True

    def get_document(self, collection: str, document_id: str) -> dict[str, Any] | None:
        snapshot = self._client.collection(collection).document(document_id).get()
        return None if not snapshot.exists else dict(snapshot.to_dict())

    def set_document(self, collection: str, document_id: str, data: dict[str, Any]) -> None:
        self._client.collection(collection).document(document_id).set(dict(data))

    def delete_document(self, collection: str, document_id: str) -> None:
        self._client.collection(collection).document(document_id).delete()

    def list_documents(self, collection: str) -> list[dict[str, Any]]:
        return [
            dict(snapshot.to_dict()) for snapshot in self._client.collection(collection).stream()
        ]

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()


def _fernet(key: str) -> Fernet:
    from cryptography.fernet import Fernet

    return Fernet(_normalize_fernet_key(key))


class FirestoreOAuthStateStore:
    def __init__(
        self,
        document_store: DocumentStoreProtocol,
        *,
        collection: str,
    ) -> None:
        self._document_store = document_store
        self._collection = collection

    def create(self, state: str, created_at_ms: int | None = None) -> None:
        import time

        timestamp = created_at_ms if created_at_ms is not None else int(time.time() * 1000)
        self._document_store.set_document(
            self._collection,
            state,
            {"state": state, "created_at_ms": timestamp},
        )

    def pop(self, state: str, max_age_ms: int, now_ms: int | None = None) -> bool:
        import time

        current = now_ms if now_ms is not None else int(time.time() * 1000)
        payload = self._document_store.get_document(self._collection, state)
        self._document_store.delete_document(self._collection, state)
        return bool(payload) and current - int(payload["created_at_ms"]) <= max_age_ms


class FirestoreInstallationStore:
    installation_key = "default"

    def __init__(
        self,
        document_store: DocumentStoreProtocol,
        *,
        collection_prefix: str,
        encryption_key: str,
    ) -> None:
        self._document_store = document_store
        self._installations_collection = f"{collection_prefix}_installations"
        self._metadata_collection = f"{collection_prefix}_runtime_metadata"
        self.oauth_states = FirestoreOAuthStateStore(
            document_store,
            collection=f"{collection_prefix}_oauth_states",
        )
        self._fernet = _fernet(encryption_key)

    def save_installation(self, installation: StoredInstallation) -> None:
        self._document_store.set_document(
            self._installations_collection,
            self.installation_key,
            {
                "access_token": self._fernet.encrypt(
                    installation.access_token.encode("utf-8")
                ).decode("utf-8"),
                "refresh_token": self._fernet.encrypt(
                    installation.refresh_token.encode("utf-8")
                ).decode("utf-8"),
                "expires_at_ms": installation.expires_at_ms,
                "scope": list(installation.scope),
            },
        )

    def load_installation(self) -> StoredInstallation | None:
        payload = self._document_store.get_document(
            self._installations_collection,
            self.installation_key,
        )
        if payload is None:
            return None
        return StoredInstallation(
            access_token=self._fernet.decrypt(payload["access_token"].encode("utf-8")).decode(
                "utf-8"
            ),
            refresh_token=self._fernet.decrypt(payload["refresh_token"].encode("utf-8")).decode(
                "utf-8"
            ),
            expires_at_ms=int(payload["expires_at_ms"]),
            scope=tuple(str(item) for item in payload["scope"]),
        )

    def set_metadata(self, key: str, value: str) -> None:
        self._document_store.set_document(
            self._metadata_collection,
            key,
            {"key": key, "value": value},
        )

    def get_metadata(self, key: str) -> str | None:
        payload = self._document_store.get_document(self._metadata_collection, key)
        return None if payload is None else str(payload["value"])

    def close(self) -> None:
        self._document_store.close()


class FirestoreWebhookReceiptStore:
    def __init__(
        self,
        document_store: DocumentStoreProtocol,
        *,
        collection: str,
    ) -> None:
        self._document_store = document_store
        self._collection = collection

    def reserve(self, webhook_id: str, payload_sha256: str, received_at_ms: int) -> ReceiptResult:
        created = self._document_store.create_document(
            self._collection,
            webhook_id,
            {
                "webhook_id": webhook_id,
                "payload_sha256": payload_sha256,
                "received_at_ms": received_at_ms,
                "status": "reserved",
            },
        )
        if created:
            return ReceiptResult.NEW
        existing = self._document_store.get_document(self._collection, webhook_id)
        if existing:
            status = str(existing.get("status", "reserved"))
            age_ms = received_at_ms - int(existing["received_at_ms"])
            if status == "completed" or age_ms <= 5 * 60 * 1000:
                if existing["payload_sha256"] == payload_sha256:
                    return ReceiptResult.DUPLICATE
                return ReceiptResult.CONFLICT
            self._document_store.set_document(
                self._collection,
                webhook_id,
                {
                    "webhook_id": webhook_id,
                    "payload_sha256": payload_sha256,
                    "received_at_ms": received_at_ms,
                    "status": "reserved",
                },
            )
            return ReceiptResult.NEW
        return ReceiptResult.CONFLICT

    def complete(self, webhook_id: str, payload_sha256: str) -> None:
        existing = self._document_store.get_document(self._collection, webhook_id)
        if existing and existing["payload_sha256"] == payload_sha256:
            updated = dict(existing)
            updated["status"] = "completed"
            self._document_store.set_document(self._collection, webhook_id, updated)

    def release(self, webhook_id: str, payload_sha256: str) -> None:
        existing = self._document_store.get_document(self._collection, webhook_id)
        if existing and existing["payload_sha256"] == payload_sha256:
            self._document_store.delete_document(self._collection, webhook_id)

    def close(self) -> None:
        self._document_store.close()


class FirestoreApprovalLedger:
    def __init__(
        self,
        document_store: DocumentStoreProtocol,
        *,
        collection: str,
    ) -> None:
        self._document_store = document_store
        self._collection = collection

    def append(self, record: SyntheticApprovalRecord) -> None:
        self._document_store.set_document(
            self._collection,
            record.approval_id,
            record.model_dump(),
        )

    @property
    def records(self) -> list[SyntheticApprovalRecord]:
        payloads = self._document_store.list_documents(self._collection)
        return [SyntheticApprovalRecord.model_validate(payload) for payload in payloads]


class FirestoreProductBriefStore(InMemoryProductBriefStore):
    def __init__(
        self,
        document_store: DocumentStoreProtocol,
        *,
        collection_prefix: str,
    ) -> None:
        super().__init__(document_store, collection_prefix=collection_prefix)


def build_installation_store(config: LiveProductAgentConfig) -> InstallationStoreProtocol:
    if config.storage_backend == "firestore":
        return FirestoreInstallationStore(
            FirestoreDocumentStore(
                project_id=config.firestore_project_id,
                database_id=config.firestore_database_id,
            ),
            collection_prefix=config.firestore_collection_prefix,
            encryption_key=config.token_encryption_key,
        )
    return InstallationStore(config.database_path, config.token_encryption_key)


def build_receipt_store(config: LiveProductAgentConfig) -> ReceiptStoreProtocol:
    if config.storage_backend == "firestore":
        return FirestoreWebhookReceiptStore(
            FirestoreDocumentStore(
                project_id=config.firestore_project_id,
                database_id=config.firestore_database_id,
            ),
            collection=f"{config.firestore_collection_prefix}_webhook_receipts",
        )
    return WebhookReceiptStore(config.database_path)


def build_product_brief_store(config: LiveProductAgentConfig) -> ProductBriefStoreProtocol:
    if config.storage_backend == "firestore":
        return FirestoreProductBriefStore(
            FirestoreDocumentStore(
                project_id=config.firestore_project_id,
                database_id=config.firestore_database_id,
            ),
            collection_prefix=config.firestore_collection_prefix,
        )
    return InMemoryProductBriefStore(collection_prefix=config.firestore_collection_prefix)
