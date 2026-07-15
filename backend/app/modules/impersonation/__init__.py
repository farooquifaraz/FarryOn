"""Admin "login as user" — a short-lived, non-refreshable access token
flagged with an ``act`` (acting-as) claim, so it's unambiguous in every
downstream check and audit-logged action which admin is really behind it.
"""
