import json
import logging
import os
from datetime import datetime

from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError

CONTAINER_NAME = "discovereu-monitor"
BLOB_NAME = "meetup-state.json"


def _get_blob_client():
    connection_string = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = service_client.get_container_client(CONTAINER_NAME)

    # Create container if it doesn't exist
    try:
        container_client.get_container_properties()
    except ResourceNotFoundError:
        container_client.create_container()
        logging.info("Created blob container '%s'", CONTAINER_NAME)

    return container_client.get_blob_client(BLOB_NAME)


def load_state() -> dict | None:
    blob_client = _get_blob_client()
    try:
        data = blob_client.download_blob().readall()
        return json.loads(data)
    except ResourceNotFoundError:
        logging.info("No existing state found — this is the first run")
        return None


def save_state(hash_value: str, meetups: list[dict]) -> None:
    blob_client = _get_blob_client()
    state = {
        "hash": hash_value,
        "meetups": meetups,
        "last_checked": datetime.utcnow().isoformat() + "Z",
        "last_changed": datetime.utcnow().isoformat() + "Z",
    }
    blob_client.upload_blob(
        json.dumps(state, ensure_ascii=False, indent=2),
        overwrite=True,
    )
    logging.info("State saved to blob storage")


def update_last_checked() -> None:
    """Update only the last_checked timestamp without changing the hash or meetups."""
    blob_client = _get_blob_client()
    try:
        data = blob_client.download_blob().readall()
        state = json.loads(data)
        state["last_checked"] = datetime.utcnow().isoformat() + "Z"
        blob_client.upload_blob(
            json.dumps(state, ensure_ascii=False, indent=2),
            overwrite=True,
        )
    except ResourceNotFoundError:
        pass
