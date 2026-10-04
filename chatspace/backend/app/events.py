"""WebSocket protocol: every server->client frame is {"type": <one of these>, ...}."""
MESSAGE_CREATED = "message.created"
MESSAGE_UPDATED = "message.updated"
MESSAGE_DELETED = "message.deleted"
REACTION_ADDED = "reaction.added"
REACTION_REMOVED = "reaction.removed"
USER_ONLINE = "user.online"
USER_OFFLINE = "user.offline"
TYPING_STARTED = "typing.started"
TYPING_STOPPED = "typing.stopped"
CHANNEL_CREATED = "channel.created"
CHANNEL_UPDATED = "channel.updated"
NOTIFICATION_CREATED = "notification.created"


def channel_room(channel_id: str) -> str:
    return f"channel:{channel_id}"


def workspace_room(workspace_id: str) -> str:
    return f"workspace:{workspace_id}"


def user_room(user_id: str) -> str:
    return f"user:{user_id}"
