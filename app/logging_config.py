import logging


def configure_logging(level: str) -> None:
    try:
        numeric_level = logging.getLevelNamesMapping()[level.upper()]
    except KeyError as error:
        raise LookupError(f"Unsupported LOG_LEVEL: {level}") from error

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger().setLevel(numeric_level)
