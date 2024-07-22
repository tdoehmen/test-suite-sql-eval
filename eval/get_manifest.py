"""Manifest utils."""
from manifest import Manifest
from manifest.connections.client_pool import ClientConnection


def get_manifest(
    manifest_client: str,
    manifest_connection: str,
    manifest_engine: str,
) -> Manifest:
    """Get manifest engine."""
    if manifest_client in {"openai", "openaichat", "openai_mock", "openrouter"}:
        manifest = Manifest(
            client_name=manifest_client,
            engine=manifest_engine,
        )
    elif manifest_client in {"huggingface"}:
        manifest = Manifest(
            client_pool=[
                ClientConnection(
                    client_name=manifest_client,
                    client_connection=manifest_conn,
                )
                for manifest_conn in manifest_connection.split(";")
            ],
        )
    else:
        raise ValueError(f"Unknown manifest client {manifest_client}")
    return manifest
