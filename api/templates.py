import json

from fastapi.templating import Jinja2Templates

from config import get_language
from i18n import SUPPORTED_LANGUAGES, get_js_strings, translate

templates = Jinja2Templates(directory="templates")
templates.env.filters["fromjson"] = json.loads
templates.env.globals["t"] = lambda key: translate(get_language(), key)
templates.env.globals["lang_js"] = lambda: get_js_strings(get_language())
templates.env.globals["SUPPORTED_LANGUAGES"] = SUPPORTED_LANGUAGES
