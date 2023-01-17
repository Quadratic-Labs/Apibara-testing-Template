from dynaconf import Dynaconf

config = Dynaconf(settings_files=["config.toml"], environments=True)
