import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """
    Central logging configuration for the entire dertwin application.
    """

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=(
            "%(asctime)s | "
            "%(levelname)-8s | "
            "%(name)s | "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Make asyncio less noisy unless debugging deeply
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialized | level=%s",
        level.upper()
    )

    logging.getLogger("pymodbus").propagate = False
