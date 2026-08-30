"""
Main entry point for the Bot Prototype application.
"""

from src.bot import Bot


def main():
    """Initialize and run the bot."""
    bot = Bot()
    bot.start()


if __name__ == "__main__":
    main()