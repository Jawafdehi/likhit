import sentry_sdk

SENTRY_DSN = "https://examplePublicKey@o0.ingest.sentry.io/0"

sentry_sdk.init(
    dsn=SENTRY_DSN,
    traces_sample_rate=1.0,
    send_default_pii=False,
    environment="production",
)
