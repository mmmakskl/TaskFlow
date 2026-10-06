import os

from alembic.config import Config

from alembic import command


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    if not os.environ.get("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is required")
    command.upgrade(Config("alembic.ini"), "head")


if __name__ == "__main__":
    main()
