"""HTTP-клиент для вызовов API образовательной платформы."""

import logging
import httpx
from typing import Optional

from app.config import EDUCATION_API_URL, EDUCATION_API_KEY

logger = logging.getLogger(__name__)

# Типы для ответов
WhoamiResponse = dict
BalanceResponse = dict
PaymentResponse = dict


class PlatformAPI:
    """Клиент для взаимодействия с бэкендом образовательной платформы."""

    def __init__(self) -> None:
        self.base_url = (EDUCATION_API_URL or "").rstrip("/")
        self.headers = {
            "X-Telegram-Bot-Key": EDUCATION_API_KEY,
            "Content-Type": "application/json",
        }

    async def whoami(self, tg_username: str) -> WhoamiResponse:
        """GET /telegram/whoami/{tg_username}"""

        url = f"{self.base_url}/telegram/whoami/{tg_username.lstrip('@')}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=self.headers, timeout=15.0)
                if resp.status_code == 200:
                    return resp.json()
                logger.error(f"whoami error {resp.status_code}: {resp.text}")
                return {"found": False, "error": f"HTTP {resp.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"whoami network error: {e}")
            return {"found": False, "error": str(e)}

    async def get_student_balance(self, student_id: int, limit: int = 5) -> BalanceResponse:
        """GET /telegram/student/{student_id}/balance?limit=N"""

        url = f"{self.base_url}/telegram/student/{student_id}/balance"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url, headers=self.headers, params={"limit": limit}, timeout=15.0
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.error(f"balance error {resp.status_code}: {resp.text}")
                return {"error": f"HTTP {resp.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"balance network error: {e}")
            return {"error": str(e)}

    async def get_payment_stats(
        self, student_id: int, page: int = 1, page_size: int = 5
    ) -> dict:
        """GET /telegram/student/{student_id}/payment-stats?page=N&page_size=N"""

        url = f"{self.base_url}/telegram/student/{student_id}/payment-stats"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    headers=self.headers,
                    params={"page": page, "page_size": page_size},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    return resp.json()
                logger.error(f"payment-stats error {resp.status_code}: {resp.text}")
                return {"error": f"HTTP {resp.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"payment-stats network error: {e}")
            return {"error": str(e)}

    async def confirm_payment(
        self,
        teacher_tg_username: str,
        student_tg_username: str,
        amount: int,
        payment_type: str = "per_lesson",
        comment: str = "",
    ) -> PaymentResponse:
        """POST /telegram/confirm-payment"""

        url = f"{self.base_url}/telegram/confirm-payment"
        payload = {
            "teacher_tg_username": teacher_tg_username,
            "student_tg_username": student_tg_username,
            "amount": amount,
            "payment_type": payment_type,
            "comment": comment,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=self.headers, timeout=20.0)
                if resp.status_code == 200:
                    return resp.json()
                detail = resp.json().get("detail", resp.text[:200]) if resp.text else str(resp.status_code)
                logger.error(f"confirm_payment error {resp.status_code}: {detail}")
                return {"error": detail}
        except httpx.RequestError as e:
            logger.error(f"confirm_payment network error: {e}")
            return {"error": str(e)}

    async def reject_payment(
        self,
        teacher_tg_username: str,
        student_tg_username: str,
        comment: str = "",
    ) -> dict:
        """POST /telegram/reject-payment"""

        url = f"{self.base_url}/telegram/reject-payment"
        payload = {
            "teacher_tg_username": teacher_tg_username,
            "student_tg_username": student_tg_username,
            "amount": 0,
            "payment_type": "per_lesson",
            "comment": comment,
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=self.headers, timeout=20.0)
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"HTTP {resp.status_code}"}
        except httpx.RequestError as e:
            return {"error": str(e)}

    async def register_chat(self, tg_username: str, chat_id: int) -> dict:
        """POST /telegram/register-chat — сохранить chat_id учителя."""

        url = f"{self.base_url}/telegram/register-chat"
        payload = {"tg_username": tg_username, "chat_id": chat_id}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=self.headers, timeout=10.0)
                if resp.status_code == 200:
                    return resp.json()
                logger.error(f"register_chat error {resp.status_code}: {resp.text}")
                return {"found": False, "error": f"HTTP {resp.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"register_chat network error: {e}")
            return {"found": False, "error": str(e)}

    async def get_teacher_chat(self, student_id: int) -> dict:
        """GET /telegram/student/{student_id}/teacher-chat — chat_id учителя ученика."""

        url = f"{self.base_url}/telegram/student/{student_id}/teacher-chat"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=self.headers, timeout=10.0)
                if resp.status_code == 200:
                    return resp.json()
                logger.error(f"get_teacher_chat error {resp.status_code}: {resp.text}")
                return {"found": False, "error": f"HTTP {resp.status_code}"}
        except httpx.RequestError as e:
            logger.error(f"get_teacher_chat network error: {e}")
            return {"found": False, "error": str(e)}

    async def forgot_password(self, tg_username: str) -> dict:
        """POST /telegram/forgot-password — запросить сброс пароля."""

        url = f"{self.base_url}/telegram/forgot-password"
        payload = {"tg_username": tg_username.lstrip("@")}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload, headers=self.headers, timeout=15.0)
                if resp.status_code == 200:
                    return resp.json()
                logger.error(f"forgot_password error {resp.status_code}: {resp.text}")
                return {"ok": False, "message": f"Ошибка сервера ({resp.status_code})"}
        except httpx.RequestError as e:
            logger.error(f"forgot_password network error: {e}")
            return {"ok": False, "message": "Не удалось связаться с сервером."}


# Глобальный экземпляр
api = PlatformAPI()
