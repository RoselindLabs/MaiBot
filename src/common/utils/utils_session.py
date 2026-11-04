from typing import Any, Mapping, Optional

import hashlib


class SessionUtils:
    @staticmethod
    def private_peer_id(
        user_id: str,
        account_id: Optional[str],
        additional_config: Optional[Mapping[str, Any]],
    ) -> str:
        """区分私聊发送者与会话对端；自身消息必须提供明确的接收者。"""
        if not account_id or user_id != account_id:
            return user_id
        target = (additional_config or {}).get("platform_io_target_user_id")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("自身私聊消息缺少明确的接收者，不能按发送者创建会话")
        return target.strip()

    @staticmethod
    def calculate_session_id(
        platform: str,
        *,
        user_id: Optional[str] = None,
        group_id: Optional[str] = None,
        account_id: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> str:
        """计算session_id

        Args:
            platform: 平台名称
            user_id: 用户ID（如果是私聊）
            group_id: 群ID（如果是群聊）
            account_id: 当前平台账号 ID，可选
            scope: 当前路由作用域，可选
        Returns:
            str: 计算得到的会话ID
        Raises:
            ValueError: 当 user_id 和 group_id 都未提供时抛出
        """
        if not user_id and not group_id:
            raise ValueError("UserID 或 GroupID 必须提供其一")

        route_components = []
        if account_id:
            route_components.append(f"account:{account_id}")
        if scope:
            route_components.append(f"scope:{scope}")

        if group_id:
            components = [platform, *route_components, group_id]
        else:
            components = [platform, *route_components, user_id, "private"]
        return hashlib.md5("_".join(components).encode()).hexdigest()
