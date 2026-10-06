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

# Content-free "something happened in this channel" ping. Public channels: sent to the workspace room;
# private channels: sent only to the member's own user room. Drives unread badges for channels the
# client has not subscribed to; it never carries message text.
CHANNEL_ACTIVITY = "channel.activity"
# The user read a channel (on another tab/device): clear the badge everywhere.
CHANNEL_READ = "channel.read"
