"""Entry point: loads config and starts the bot."""
from hack_match.bot import create_client
from hack_match.config import load_dev_guild_id, load_signup_deadline, load_token


def main() -> None:
    token = load_token()
    deadline = load_signup_deadline()
    dev_guild_id = load_dev_guild_id()
    client = create_client(deadline, dev_guild_id)
    client.run(token)


if __name__ == "__main__":
    main()
