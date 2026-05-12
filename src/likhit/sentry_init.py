import sentry_sdk

SENTRY_DSN = "https://7a523fc5dfd40de79932c1f00a9bc6f9@o4511364048027648.ingest.de.sentry.io/4511374818672720"

sentry_sdk.init(
    dsn=SENTRY_DSN,
    traces_sample_rate=1.0,
    send_default_pii=False,
    environment="production",
)
