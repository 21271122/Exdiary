import sys
import warnings
import yaml
import socket
import threading
from pathlib import Path
from typing import Any
from flask import Flask, g
from pydantic import BaseModel, Field

from lib.logger import init_logger, get_logger
from lib.llm import LLMClient
from lib.repositories.yaml_experiment import YamlExperimentRepository
from lib.repositories.yaml_analysis import YamlAnalysisRepository
from lib.repositories.yaml_thread import ThreadRepository
from lib.repositories.yaml_favorites import YamlFavoritesRepository
from lib.repositories.yaml_update_log import YamlUpdateLogRepository
from lib.services.experiment import ExperimentService
from lib.services.extraction import ExtractionService
from lib.services.analysis import AnalysisService
from lib.services.template import TemplateService

from routes.dashboard import dashboard_bp
from routes.experiment import experiment_bp
from routes.api_experiment import api_experiment_bp
from routes.api_agent import api_agent_bp
from routes.api_child import api_child_bp
from routes.api_analysis import api_analysis_bp
from routes.api_search import api_search_bp
from routes.api_favorites import api_favorites_bp
from routes.api_upload import api_upload_bp
from routes.settings import settings_bp
from routes.templates import templates_bp
from routes.uploads import uploads_bp
from routes.pages import pages_bp

BASE_DIR = Path(__file__).parent
SETTINGS_PATH = BASE_DIR / "config.yaml"


class Settings(BaseModel):
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    DEEPSEEK_ANALYZE_MODEL: str = "deepseek-v4-pro"
    PORT: int = Field(default=5000, ge=1024, le=65535)
    HOST: str = "0.0.0.0"
    GUI: str = "true"

    @classmethod
    def _warn_unknown_model(cls, model_name: str, field_name: str) -> None:
        if not model_name.startswith("deepseek-"):
            warnings.warn(
                f"{field_name}='{model_name}' 不是已知的 DeepSeek 模型前缀，请确认配置正确。",
                stacklevel=2,
            )

    def validate_model_names(self) -> None:
        self._warn_unknown_model(self.DEEPSEEK_MODEL, "DEEPSEEK_MODEL")
        self._warn_unknown_model(self.DEEPSEEK_ANALYZE_MODEL, "DEEPSEEK_ANALYZE_MODEL")


def _parse_dotenv(path: Path) -> dict[str, str]:
    cfg: dict[str, str] = {}
    if not path.exists():
        return cfg
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                cfg[key] = value
    return cfg


def load_settings() -> Settings:
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        settings = Settings(**raw)
        settings.validate_model_names()
        return settings
    old = _parse_dotenv(BASE_DIR / ".env")
    raw = {
        "DEEPSEEK_API_KEY": old.get("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_MODEL": old.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        "DEEPSEEK_ANALYZE_MODEL": old.get("DEEPSEEK_ANALYZE_MODEL", "deepseek-v4-pro"),
        "PORT": int(old.get("PORT", "5000")),
        "HOST": old.get("HOST", "0.0.0.0"),
        "GUI": old.get("GUI", "true"),
    }
    settings = Settings(**raw)
    save_settings(settings)
    return settings


def save_settings(data: Settings | dict[str, Any]) -> None:
    global config
    if isinstance(data, dict):
        # 强制类型转换，防止表单提交的字符串值（如 PORT="5000"）被 Pydantic 拒绝
        normalized: dict[str, Any] = {}
        for k, v in data.items():
            if k == "PORT" and isinstance(v, str):
                normalized[k] = int(v)
            elif k == "GUI":
                normalized[k] = str(v).lower() if isinstance(v, (bool, int)) else str(v).strip()
            else:
                normalized[k] = str(v).strip() if isinstance(v, str) else v
        data = Settings(**normalized)
    clean: dict[str, Any] = data.model_dump()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(clean, f, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2)
    config = clean


config = load_settings().model_dump()
init_logger(BASE_DIR / "experiments")


def get_extract_llm():
    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key: return None
    return LLMClient(api_key=api_key, model=config.get("DEEPSEEK_MODEL", "deepseek-v4-flash"))


def get_analyze_llm():
    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key: return None
    model = config.get("DEEPSEEK_ANALYZE_MODEL", config.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    return LLMClient(api_key=api_key, model=model)


def get_agent_llm():
    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key: return None
    return LLMClient(api_key=api_key, model=config.get("DEEPSEEK_MODEL", "deepseek-v4-flash"))


# ---- 应用工厂 ----
def create_app():
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    # ---- 仓储层 ----
    exp_repo = YamlExperimentRepository(str(BASE_DIR / "experiments"))
    analysis_repo = YamlAnalysisRepository(str(BASE_DIR / "experiments" / "_analysis_history"))
    thread_repo = ThreadRepository(str(BASE_DIR / "experiments" / "_threads"))
    favorites_repo = YamlFavoritesRepository(str(BASE_DIR / "experiments" / "_favorites.yaml"))
    update_log_repo = YamlUpdateLogRepository(str(BASE_DIR / "experiments" / "_update_logs"))

    # ---- 服务层 ----
    experiment_svc = ExperimentService(exp_repo, update_log_repo, favorites_repo, BASE_DIR)
    extraction_svc = ExtractionService(None)
    analysis_svc = AnalysisService(exp_repo, analysis_repo, get_analyze_llm())
    template_svc = TemplateService(str(BASE_DIR / "experiment_templates"))

    # ---- flask.g 注入 ----
    @app.before_request
    def inject_services():
        g.config = config
        g.exp_repo = exp_repo
        g.analysis_repo = analysis_repo
        g.thread_repo = thread_repo
        g.favorites_repo = favorites_repo
        g.update_log_repo = update_log_repo
        g.experiment_svc = experiment_svc
        g.extraction_svc = extraction_svc
        g.analysis_svc = analysis_svc
        g.template_svc = template_svc
        g.base_dir = BASE_DIR
        g.get_extract_llm = get_extract_llm
        g.get_analyze_llm = get_analyze_llm
        g.get_agent_llm = get_agent_llm

    # ---- 注册蓝图 ----
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(experiment_bp, url_prefix="/experiments")
    app.register_blueprint(pages_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(templates_bp)
    app.register_blueprint(uploads_bp)

    app.register_blueprint(api_experiment_bp, url_prefix="/api")
    app.register_blueprint(api_agent_bp, url_prefix="/api/agent")
    app.register_blueprint(api_child_bp, url_prefix="/api")
    app.register_blueprint(api_analysis_bp, url_prefix="/api")
    app.register_blueprint(api_search_bp, url_prefix="/api")
    app.register_blueprint(api_favorites_bp, url_prefix="/api")
    app.register_blueprint(api_upload_bp, url_prefix="/api")

    return app, config


# ---- 启动 ----
if __name__ == "__main__":
    app, config = create_app()
    port = int(config.get("PORT", 5000))
    host = config.get("HOST", "0.0.0.0")
    use_gui = config.get("GUI", "true").lower() in ("true", "1", "yes")

    log = get_logger()
    if log:
        log.system("info", "startup", port=port, gui=config.get("GUI", "true"))

    model = config.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    analyze_model = config.get("DEEPSEEK_ANALYZE_MODEL", "deepseek-v4-pro")

    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    print(f"  Exdiary")
    print(f"  Local:    http://127.0.0.1:{port}")
    print(f"  Network:  http://{lan_ip}:{port}")
    print(f"  Extract:  {model}")
    print(f"  Analyze:  {analyze_model}")

    if "--headless" in sys.argv or not use_gui:
        print(f"  Mode:     headless (web only)")
        app.run(host=host, port=port, debug=True)
    else:
        try:
            import webview
        except ImportError:
            print("  pywebview not installed. Run: pip install pywebview")
            print("  Falling back to web mode...")
            app.run(host=host, port=port, debug=True)
            sys.exit()

        def run_flask():
            app.run(host=host, port=port, debug=False, use_reloader=False)

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        print(f"  Mode:     native desktop window")
        webview.create_window(
            title="Exdiary — 实验记录",
            url=f"http://127.0.0.1:{port}",
            width=1100, height=750, min_size=(800, 500),
            text_select=True,
        )
        webview.start()
        sys.exit(0)
