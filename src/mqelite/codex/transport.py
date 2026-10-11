"""Showcase excerpt: transport exception types only; no live RPC transport."""


class WebSocketProtocolError(ConnectionError):
    """The local app-server WebSocket violated a protocol invariant."""

class WebSocketMessageTooLarge(WebSocketProtocolError):
    """An inbound frame or reassembled message exceeded the read bound."""
