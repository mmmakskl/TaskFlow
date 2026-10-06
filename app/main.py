from app.config.settings import Settings
from app.web.application import create_app

app = create_app(Settings())
