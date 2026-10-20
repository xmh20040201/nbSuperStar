import configparser
import os

CONFIG_PATH = "config.ini"


def load_config() -> dict:
    """Read config.ini and return all sections as a dict."""
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf8")
    result = {}
    for section in config.sections():
        result[section] = dict(config.items(section))
    return result


def save_config(config_data: dict) -> None:
    """Write config data back to config.ini, preserving existing keys."""
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH, encoding="utf8")

    for section, items in config_data.items():
        if not config.has_section(section):
            config.add_section(section)
        for key, value in items.items():
            if value is not None:
                config.set(section, key, str(value))

    with open(CONFIG_PATH, "w", encoding="utf8") as f:
        config.write(f)
