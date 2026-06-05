import os


os.environ.setdefault("PROOF_OF_CHARGE_SKIP_DOTENV", "1")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("REQUIRE_DATABASE", None)
