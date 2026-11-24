import os
import urllib.parse

import requests

from backend.config import settings
from backend.services.exceptions import AppException
from backend.services.logger import get_logger

logger = get_logger("linkedin_service")


class LinkedInServiceError(AppException):
    default_code = "LINKEDIN_ERROR"
    default_status_code = 502
    default_message = "LinkedIn API operation failed."


class LinkedInTransientError(LinkedInServiceError):
    default_code = "LINKEDIN_TRANSIENT_ERROR"
    default_status_code = 503
    default_message = "Transient LinkedIn error; eligible for retry."

    def __init__(self, message: str, retry_after: int | None = None, http_status: int | None = None):
        super().__init__(message=message, status_code=503)
        self.retry_after = retry_after
        self.http_status = http_status


class LinkedInFatalError(LinkedInServiceError):
    default_code = "LINKEDIN_FATAL_ERROR"
    default_status_code = 400
    default_message = "Non-retryable LinkedIn error."

    def __init__(self, message: str, http_status: int | None = None, details: dict | None = None):
        super().__init__(message=message, status_code=http_status or 400, details=details)
        self.http_status = http_status


class LinkedInAuthError(LinkedInFatalError):
    default_code = "LINKEDIN_AUTH_ERROR"
    default_status_code = 401
    default_message = "LinkedIn token expired or invalid, please reconnect your account."


class LinkedInService:
    AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
    TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
    USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
    POSTS_URL = "https://api.linkedin.com/rest/posts"

    def get_authorization_url(self, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": settings.LINKEDIN_CLIENT_ID,
            "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
            "state": state,
            "scope": "openid profile w_member_social",
        }
        return f"{self.AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code_for_token(self, code: str) -> dict:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
            "client_id": settings.LINKEDIN_CLIENT_ID,
            "client_secret": settings.LINKEDIN_CLIENT_SECRET,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}

        try:
            resp = requests.post(self.TOKEN_URL, data=payload, headers=headers, timeout=30)
        except requests.RequestException as e:
            logger.error("linkedin_token_exchange_network_error", error=str(e))
            raise LinkedInTransientError(f"Network error exchanging OAuth code: {str(e)}") from e

        if not resp.ok:
            logger.error(
                "linkedin_token_exchange_failed",
                status_code=resp.status_code,
                body=resp.text,
            )
            raise LinkedInFatalError(
                f"Failed to exchange OAuth code: {resp.text}",
                http_status=resp.status_code,
            )

        return resp.json()

    def get_user_info(self, access_token: str) -> dict:
        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = requests.get(self.USERINFO_URL, headers=headers, timeout=20)
        except requests.RequestException as e:
            logger.error("linkedin_userinfo_network_error", error=str(e))
            raise LinkedInTransientError(f"Network error fetching userinfo: {str(e)}") from e

        if resp.status_code in (401, 403):
            raise LinkedInAuthError("LinkedIn token is expired or lacks required profile scopes.")

        if not resp.ok:
            raise LinkedInFatalError(
                f"LinkedIn userinfo request failed: {resp.text}",
                http_status=resp.status_code,
            )

        data = resp.json()
        sub = data.get("sub")
        if not sub:
            raise LinkedInFatalError("OpenID userinfo response missing 'sub' identifier.")

        return {
            "sub": sub,
            "author_urn": f"urn:li:person:{sub}",
            "name": data.get("name", "LinkedIn User"),
            "email": data.get("email"),
            "picture": data.get("picture"),
        }

    def _upload_image(self, access_token: str, author_urn: str, file_path: str, mime_type: str = "image/png") -> str:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Linkedin-Version": "202611",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        init_resp = requests.post(
            "https://api.linkedin.com/rest/images?action=initializeUpload",
            json={"initializeUploadRequest": {"owner": author_urn}},
            headers=headers,
            timeout=30,
        )
        if not init_resp.ok:
            raise LinkedInTransientError(f"Failed to initialize image upload on LinkedIn: {init_resp.text}")

        val = init_resp.json().get("value", {})
        upload_url = val.get("uploadUrl")
        image_urn = val.get("image")
        if not upload_url or not image_urn:
            raise LinkedInFatalError("LinkedIn initializeUpload response missing image URN or uploadUrl.")

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        put_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": mime_type,
        }
        put_resp = requests.put(upload_url, data=file_bytes, headers=put_headers, timeout=60)
        if put_resp.status_code not in (200, 201):
            raise LinkedInTransientError(f"Failed to upload image binary to LinkedIn: {put_resp.status_code} {put_resp.text}")

        logger.info("linkedin_image_uploaded_successfully", image_urn=image_urn, size=len(file_bytes))
        return image_urn

    def _upload_document(self, access_token: str, author_urn: str, file_path: str, mime_type: str = "application/pdf") -> str:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Linkedin-Version": "202611",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        init_resp = requests.post(
            "https://api.linkedin.com/rest/documents?action=initializeUpload",
            json={"initializeUploadRequest": {"owner": author_urn}},
            headers=headers,
            timeout=30,
        )
        if not init_resp.ok:
            raise LinkedInTransientError(f"Failed to initialize document upload on LinkedIn: {init_resp.text}")

        val = init_resp.json().get("value", {})
        upload_url = val.get("uploadUrl")
        doc_urn = val.get("document")
        if not upload_url or not doc_urn:
            raise LinkedInFatalError("LinkedIn initializeUpload response missing document URN or uploadUrl.")

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        put_headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": mime_type,
        }
        put_resp = requests.put(upload_url, data=file_bytes, headers=put_headers, timeout=60)
        if put_resp.status_code not in (200, 201):
            raise LinkedInTransientError(f"Failed to upload document binary to LinkedIn: {put_resp.status_code} {put_resp.text}")

        logger.info("linkedin_document_uploaded_successfully", doc_urn=doc_urn, size=len(file_bytes))
        return doc_urn

    def publish_post(
        self,
        access_token: str,
        author_urn: str,
        content: str,
        media_items: list[dict] | None = None,
    ) -> tuple[str, dict]:
        # If media attachments are present, upload and publish via LinkedIn REST Posts API
        if media_items:
            image_urns: list[str] = []
            doc_urns: list[str] = []
            for item in media_items:
                f_path = item.get("file_path")
                m_type = (item.get("type") or "").lower()
                m_name = (item.get("name") or "").lower()

                if not f_path or not os.path.exists(f_path):
                    continue

                if "image" in m_type or any(m_name.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]):
                    img_urn = self._upload_image(access_token, author_urn, f_path, m_type or "image/png")
                    image_urns.append(img_urn)
                else:
                    doc_urn = self._upload_document(access_token, author_urn, f_path, m_type or "application/pdf")
                    doc_urns.append(doc_urn)

            if image_urns or doc_urns:
                rest_headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Linkedin-Version": "202611",
                    "X-Restli-Protocol-Version": "2.0.0",
                    "Content-Type": "application/json",
                }
                post_body = {
                    "author": author_urn,
                    "commentary": content,
                    "visibility": "PUBLIC",
                    "distribution": {
                        "feedDistribution": "MAIN_FEED",
                        "targetEntities": [],
                        "thirdPartyDistributionChannels": [],
                    },
                    "lifecycleState": "PUBLISHED",
                    "isReshareDisabledByAuthor": False,
                }
                if image_urns:
                    if len(image_urns) == 1:
                        post_body["content"] = {
                            "media": {
                                "id": image_urns[0],
                                "title": media_items[0].get("name", "Image"),
                            }
                        }
                    else:
                        post_body["content"] = {
                            "multiImage": {
                                "images": [{"id": urn} for urn in image_urns]
                            }
                        }
                elif doc_urns:
                    post_body["content"] = {
                        "media": {
                            "id": doc_urns[0],
                            "title": media_items[0].get("name", "Document"),
                        }
                    }

                try:
                    resp = requests.post("https://api.linkedin.com/rest/posts", json=post_body, headers=rest_headers, timeout=30)
                except (requests.Timeout, requests.ConnectionError) as e:
                    logger.warn("linkedin_publish_timeout_or_conn_error", error=str(e))
                    raise LinkedInTransientError(f"LinkedIn request timed out or connection dropped: {str(e)}") from e
                except requests.RequestException as e:
                    raise LinkedInTransientError(f"LinkedIn request failed: {str(e)}") from e

                if resp.status_code == 201:
                    post_urn = resp.headers.get("x-restli-id") or resp.headers.get("location")
                    if not post_urn and resp.text:
                        try:
                            post_urn = resp.json().get("id")
                        except Exception:
                            pass
                    if not post_urn:
                        post_urn = f"urn:li:share:published-{hash(content)}"
                    logger.info("linkedin_post_with_media_published_successfully", post_urn=post_urn)
                    return post_urn, {"status_code": 201, "post_urn": post_urn}

                if resp.status_code in (401, 403):
                    raise LinkedInAuthError("LinkedIn token expired or unauthorized, please reconnect account.")
                if resp.status_code == 429:
                    raise LinkedInTransientError("LinkedIn API rate limit exceeded.")
                if resp.status_code >= 500:
                    raise LinkedInTransientError(f"LinkedIn server returned {resp.status_code}: {resp.text}")
                raise LinkedInFatalError(f"LinkedIn rejected post ({resp.status_code}): {resp.text}")

        # Text-only publishing path via UGC Posts
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        body = {
            "author": author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {
                        "text": content,
                    },
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC",
            },
        }

        try:
            resp = requests.post("https://api.linkedin.com/v2/ugcPosts", json=body, headers=headers, timeout=30)
        except (requests.Timeout, requests.ConnectionError) as e:
            logger.warn("linkedin_publish_timeout_or_conn_error", error=str(e))
            raise LinkedInTransientError(
                f"LinkedIn request timed out or connection dropped: {str(e)}"
            ) from e
        except requests.RequestException as e:
            raise LinkedInTransientError(f"LinkedIn request failed: {str(e)}") from e

        if resp.status_code == 201:
            post_urn = resp.headers.get("x-restli-id")
            if not post_urn and resp.text:
                try:
                    data = resp.json()
                    post_urn = data.get("id") or data.get("urn")
                except Exception:
                    pass
            if not post_urn:
                post_urn = f"urn:li:share:published-{hash(content)}"
            logger.info("linkedin_post_published_successfully", post_urn=post_urn)
            return post_urn, {"status_code": 201, "post_urn": post_urn}

        if resp.status_code == 429:
            retry_after = None
            retry_header = resp.headers.get("Retry-After")
            if retry_header and retry_header.isdigit():
                retry_after = int(retry_header)
            logger.warn("linkedin_rate_limited", retry_after=retry_after)
            raise LinkedInTransientError(
                "LinkedIn API rate limit exceeded (429).",
                retry_after=retry_after,
                http_status=429,
            )

        if resp.status_code >= 500:
            logger.error("linkedin_server_error", status_code=resp.status_code, body=resp.text)
            raise LinkedInTransientError(
                f"LinkedIn server returned {resp.status_code}: {resp.text}",
                http_status=resp.status_code,
            )

        if resp.status_code in (401, 403):
            logger.error("linkedin_auth_error", status_code=resp.status_code, body=resp.text)
            raise LinkedInAuthError(
                "LinkedIn token expired, please reconnect your LinkedIn account."
            )

        logger.error("linkedin_fatal_error", status_code=resp.status_code, body=resp.text)
        raise LinkedInFatalError(
            f"LinkedIn rejected post ({resp.status_code}): {resp.text}",
            http_status=resp.status_code,
        )

    def reconcile_ambiguous_post(
        self,
        access_token: str,
        author_urn: str,
        content_snippet: str,
    ) -> str | None:
        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Linkedin-Version": settings.LINKEDIN_API_VERSION,
                "X-Restli-Protocol-Version": "2.0.0",
            }
            params = {
                "author": author_urn,
                "q": "author",
                "count": 5,
                "sortBy": "LAST_MODIFIED",
            }
            resp = requests.get(self.POSTS_URL, params=params, headers=headers, timeout=15)
            if resp.ok:
                data = resp.json()
                for element in data.get("elements", []):
                    commentary = element.get("commentary", "")
                    snippet = content_snippet[:60].strip()
                    if snippet and snippet in commentary:
                        found_urn = element.get("id") or element.get("urn")
                        logger.info("reconciliation_found_existing_post", post_urn=found_urn)
                        return found_urn
        except Exception as e:
            logger.warn("reconciliation_query_failed", error=str(e))
        return None


linkedin_service = LinkedInService()
