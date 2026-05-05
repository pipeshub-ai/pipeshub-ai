"""SharePoint-specific test helpers for integration setup."""

import logging
import re
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from msgraph import GraphServiceClient  # type: ignore

logger = logging.getLogger("sharepoint-test-helpers")


@dataclass
class SharePointGraphClientHolder:
    """Graph app-only client from certificate text/files; call ``aclose`` after use."""

    client: GraphServiceClient
    _credential: Any
    _temp_cert_path: Optional[str] = None

    async def aclose(self) -> None:
        if self._credential is not None and hasattr(self._credential, "close"):
            await self._credential.close()
            self._credential = None
        if self._temp_cert_path:
            try:
                Path(self._temp_cert_path).unlink(missing_ok=True)
            except OSError:
                pass
            self._temp_cert_path = None


def sharepoint_build_graph_client_from_certificate_text(
    tenant_id: str,
    client_id: str,
    certificate: str,
    private_key: str,
) -> SharePointGraphClientHolder:
    """
    Build a GraphServiceClient using app-only certificate auth from raw file contents.

    Caller must ``await holder.aclose()`` when finished (cleans up temp combined PEM).
    """
    from azure.identity.aio import CertificateCredential  # type: ignore

    combined_pem = f"{private_key.strip()}\n{certificate.strip()}\n"
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".pem",
        delete=False,
        encoding="utf-8",
    )
    tmp.write(combined_pem)
    tmp.flush()
    tmp.close()
    temp_path = tmp.name

    credential = CertificateCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate_path=temp_path,
    )
    graph_client = GraphServiceClient(
        credential,
        scopes=["https://graph.microsoft.com/.default"],
    )
    return SharePointGraphClientHolder(
        client=graph_client,
        _credential=credential,
        _temp_cert_path=temp_path,
    )


def sharepoint_build_graph_client_from_certificate_files(
    tenant_id: str,
    client_id: str,
    certificate_file_path: str,
    private_key_file_path: str,
) -> SharePointGraphClientHolder:
    """
    Build a GraphServiceClient using app-only certificate auth from certificate files.

    Reads PEM contents from disk at call time. Caller must ``await holder.aclose()`` when finished.
    """
    cert_pem = Path(certificate_file_path).expanduser().read_text(encoding="utf-8")
    key_pem = Path(private_key_file_path).expanduser().read_text(encoding="utf-8")
    return sharepoint_build_graph_client_from_certificate_text(
        tenant_id=tenant_id,
        client_id=client_id,
        certificate=cert_pem,
        private_key=key_pem,
    )


def _integration_site_summary(site: Any) -> Dict[str, Any]:
    display = getattr(site, "display_name", None) or getattr(site, "name", None)
    return {
        "id": getattr(site, "id", None),
        "displayName": display,
        "name": getattr(site, "name", None),
        "webUrl": getattr(site, "web_url", None),
    }


async def integration_list_sharepoint_sites(
    graph_client: GraphServiceClient,
    *,
    exclude_onedrive_sites: bool = True,
) -> List[Dict[str, Any]]:
    """List tenant SharePoint sites (root + default /sites listing with pagination)."""
    sites_out: List[Dict[str, Any]] = []
    seen: set[str] = set()

    try:
        root_site = await graph_client.sites.by_site_id("root").get()
        if root_site and getattr(root_site, "id", None):
            sid = str(root_site.id)
            if sid not in seen:
                seen.add(sid)
                sites_out.append(_integration_site_summary(root_site))
    except Exception as exc:
        logger.warning("integration_list_sharepoint_sites: root site failed: %s", exc)

    search_results = await graph_client.sites.get()
    while search_results and getattr(search_results, "value", None):
        for site in search_results.value:
            web_url = getattr(site, "web_url", None) or ""
            parsed = urllib.parse.urlparse(web_url)
            hostname = parsed.hostname
            is_onedrive = (
                hostname is not None
                and re.fullmatch(r"[a-zA-Z0-9-]+-my\.sharepoint\.com", hostname) is not None
            )
            if exclude_onedrive_sites and is_onedrive:
                continue

            sid = getattr(site, "id", None)
            if not sid:
                continue
            sid_str = str(sid)
            if sid_str not in seen:
                seen.add(sid_str)
                sites_out.append(_integration_site_summary(site))

        next_link = getattr(search_results, "odata_next_link", None)
        if next_link:
            search_results = await graph_client.sites.with_url(next_link).get()
        else:
            break

    return sites_out


async def integration_resolve_site_graph_ids_by_display_names(
    graph_client: GraphServiceClient,
    display_names: List[str],
    *,
    exclude_onedrive_sites: bool = True,
) -> List[str]:
    """Resolve Graph site IDs for the given display names (case-insensitive match)."""
    raw_sites = await integration_list_sharepoint_sites(
        graph_client,
        exclude_onedrive_sites=exclude_onedrive_sites,
    )
    name_to_id: Dict[str, str] = {}
    lower_map: Dict[str, str] = {}
    for site in raw_sites:
        dn = (site.get("displayName") or site.get("name") or "").strip()
        sid = site.get("id")
        if not dn or not sid:
            continue
        name_to_id[dn] = str(sid)
        lower_map[dn.lower()] = str(sid)

    resolved: List[str] = []
    missing: List[str] = []
    for wanted in display_names:
        want = wanted.strip()
        sid = name_to_id.get(want) or lower_map.get(want.lower())
        if sid:
            resolved.append(sid)
        else:
            missing.append(want)

    if missing:
        sample = list(name_to_id.keys())[:25]
        raise ValueError(
            f"Could not resolve site Graph id for display name(s): {missing}. "
            f"Sample discovered site names: {sample}"
        )
    return resolved
