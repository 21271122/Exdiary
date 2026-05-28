import os
import uuid
import yaml
import socket
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory

from lib.storage import ExperimentStore, FavoritesStore, AnalysisStore, UpdateLogStore, ThreadStore
from lib.llm import LLMClient
from lib.parser import parse_notes, strip_html
from lib.analyzer import analyze_experiments
from lib.agent_v2 import AgentLoop
from lib.logger import init_logger, get_logger

BASE_DIR = Path(__file__).parent
SETTINGS_PATH = BASE_DIR / "config.yaml"

DEFAULT_SETTINGS = {
    "DEEPSEEK_API_KEY": "",
    "DEEPSEEK_MODEL": "deepseek-v4-flash",
    "DEEPSEEK_ANALYZE_MODEL": "deepseek-v4-pro",
    "PORT": "5000",
    "HOST": "0.0.0.0",
    "GUI": "true",
}


def _parse_dotenv(path: Path) -> dict:
    """One-shot migration: parse old .env file into a dict."""
    cfg = {}
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


def load_settings() -> dict:
    """Load settings from config.yaml. Migrate from .env on first run."""
    if SETTINGS_PATH.exists():
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or DEFAULT_SETTINGS

    # First run: try to migrate from old .env file
    old = _parse_dotenv(BASE_DIR / ".env")
    settings = {**DEFAULT_SETTINGS}
    for k in DEFAULT_SETTINGS:
        if k in old:
            settings[k] = old[k]
    save_settings(settings)
    return settings


def save_settings(data: dict) -> None:
    """Persist settings to config.yaml and reload global config."""
    global config
    clean = {}
    for k in DEFAULT_SETTINGS:
        clean[k] = str(data.get(k, DEFAULT_SETTINGS[k])).strip()
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        yaml.dump(clean, f, allow_unicode=True, sort_keys=False, default_flow_style=False, indent=2)
    config = clean


config = load_settings()
init_logger(BASE_DIR / "experiments")


class TemplateStore:
    """Manage experiment note templates (YAML files in experiment_templates/)."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self._seed_builtin()

    def list_all(self) -> list[dict]:
        templates = []
        for fp in sorted(self.path.glob("*.yaml")):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data:
                    templates.append({
                        "id": data.get("id", fp.stem),
                        "title": data.get("title", fp.stem),
                        "category": data.get("category", ""),
                        "description": data.get("description", ""),
                        "tags": data.get("tags", []),
                    })
            except Exception:
                continue
        return templates

    def load(self, template_id: str) -> dict | None:
        fp = self.path / f"{template_id}.yaml"
        if not fp.exists():
            return None
        with open(fp, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _seed_builtin(self):
        """Create built-in templates if the directory is empty."""
        if list(self.path.glob("*.yaml")):
            return
        for tmpl in _BUILTIN_TEMPLATES:
            fp = self.path / f"{tmpl['id']}.yaml"
            with open(fp, "w", encoding="utf-8") as f:
                yaml.dump(tmpl, f, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, indent=2)


_BUILTIN_TEMPLATES = [
    {
        "id": "photocatalysis",
        "title": "光催化降解实验",
        "category": "光催化",
        "description": "半导体光催化剂降解有机污染物的标准实验流程，适用于 TiO2/ZnO/g-C3N4 等催化剂评价",
        "tags": ["photocatalysis", "thin-film", "calcination"],
        "content": (
            "<p><strong>实验目的：</strong>研究【催化剂名称，如TiO2 P25】在【光源类型，如300W氙灯】照射下"
            "对【目标污染物，如亚甲基蓝】的光催化降解性能，确定最佳【变量，如负载量/浓度/pH】。</p>"
            "<p><strong>材料与试剂：</strong></p><ul>"
            "<li>【催化剂名称】，纯度【填写纯度】，厂家【填写厂家】</li>"
            "<li>【目标污染物】，浓度【填写浓度】</li>"
            "<li>基板：【基板类型，如2×2 cm玻璃片】</li></ul>"
            "<p><strong>实验设置【填写数量】组：</strong></p><ul>"
            "<li>组1：【变量条件1】</li><li>组2：【变量条件2】</li><li>组3：【变量条件3】</li></ul>"
            "<p><strong>实验步骤：</strong></p><ol>"
            "<li>配制【目标污染物】溶液，浓度【填写浓度】</li>"
            "<li>准备基板，清洗干燥</li>"
            "<li>配制不同【变量】的催化剂悬浮液：【填写具体配比】</li>"
            "<li>采用【涂覆方法，如浸渍提拉法】将催化剂涂覆到基板上</li>"
            "<li>在【温度】°C下煅烧【时间】小时</li>"
            "<li>使用【光源】照射样品</li>"
            "<li>每【时间间隔】分钟取样，用【测试方法，如紫外-可见分光光度计】测量【指标，如吸光度】</li></ol>"
            "<p><strong>预期结果：</strong>【填写预期】</p>"
            "<p><strong>备注：</strong>【填写其他信息】</p>"
        ),
    },
    {
        "id": "hydrothermal",
        "title": "水热/溶剂热合成",
        "category": "合成",
        "description": "水热或溶剂热法合成纳米材料、MOF、分子筛等的标准实验记录模板",
        "tags": ["hydrothermal", "synthesis", "nano"],
        "content": (
            "<p><strong>实验目的：</strong>通过水热/溶剂热法合成【目标产物名称】，研究【变量，如温度/时间/前驱体比例】"
            "对产物【性能指标，如形貌/晶相/尺寸】的影响。</p>"
            "<p><strong>材料与试剂：</strong></p><ul>"
            "<li>前驱体1：【名称】，【用量】，纯度【填写】，厂家【填写】</li>"
            "<li>前驱体2：【名称】，【用量】，纯度【填写】，厂家【填写】</li>"
            "<li>溶剂：【名称，如水/乙醇/DMF】，用量【填写】mL</li>"
            "<li>模板剂/表面活性剂：【名称】，用量【填写】</li></ul>"
            "<p><strong>实验参数：</strong></p><ul>"
            "<li>反应釜容积：【填写】mL，填充度：【填写】%</li>"
            "<li>反应温度：【填写】°C</li>"
            "<li>反应时间：【填写】小时</li>"
            "<li>升温速率：【填写】°C/min</li>"
            "<li>pH值：【填写】（如适用）</li></ul>"
            "<p><strong>实验步骤：</strong></p><ol>"
            "<li>称取前驱体，溶于溶剂中，搅拌【时间】至溶解/分散均匀</li>"
            "<li>（可选）加入模板剂，继续搅拌【时间】</li>"
            "<li>（可选）调节 pH 至【值】</li>"
            "<li>将溶液转移至反应釜内衬中</li>"
            "<li>密封反应釜，放入烘箱，【温度】°C 反应【时间】小时</li>"
            "<li>自然冷却至室温</li>"
            "<li>离心/过滤收集产物，用【溶剂】洗涤【次数】次</li>"
            "<li>在【温度】°C 下干燥【时间】小时</li></ol>"
            "<p><strong>产物表征计划：</strong>XRD / SEM / TEM / BET / 【其他】</p>"
            "<p><strong>备注：</strong>【填写其他信息】</p>"
        ),
    },
    {
        "id": "sol-gel",
        "title": "溶胶-凝胶法制备",
        "category": "合成",
        "description": "溶胶-凝胶法制备氧化物纳米粉体或薄膜的标准实验记录模板",
        "tags": ["sol-gel", "synthesis", "nano"],
        "content": (
            "<p><strong>实验目的：</strong>采用溶胶-凝胶法制备【目标产物，如TiO2/SiO2/Al2O3纳米粉体或薄膜】，"
            "研究【变量】对产物性能的影响。</p>"
            "<p><strong>材料与试剂：</strong></p><ul>"
            "<li>前驱体：【名称，如钛酸四丁酯/正硅酸乙酯】，用量【填写】mL，纯度【填写】，厂家【填写】</li>"
            "<li>溶剂：【名称，如乙醇/异丙醇】，用量【填写】mL</li>"
            "<li>水解抑制剂：【名称，如冰醋酸/乙酰丙酮】，用量【填写】mL</li>"
            "<li>水：用量【填写】mL</li>"
            "<li>催化剂（酸/碱）：【名称】，用量【填写】</li></ul>"
            "<p><strong>实验步骤：</strong></p><ol>"
            "<li>将前驱体溶于【溶剂】，搅拌【时间】</li>"
            "<li>加入水解抑制剂，搅拌【时间】</li>"
            "<li>缓慢滴加水（+催化剂），控制滴加速率【填写】</li>"
            "<li>搅拌形成溶胶（约【时间】）</li>"
            "<li>陈化：【温度】°C 静置【时间】小时，形成凝胶</li>"
            "<li>干燥：【温度】°C，【时间】小时，得干凝胶</li>"
            "<li>煅烧：【温度】°C，【时间】小时，升温速率【填写】°C/min</li>"
            "<li>研磨（如需）：研钵研磨【时间】分钟</li></ol>"
            "<p><strong>表征计划：</strong>XRD / SEM / TG-DTA / FTIR / 【其他】</p>"
            "<p><strong>备注：</strong>【填写其他信息】</p>"
        ),
    },
    {
        "id": "spin-coating",
        "title": "旋涂法制膜",
        "category": "薄膜",
        "description": "旋涂法制备薄膜的标准实验记录，适用于钙钛矿/聚合物/氧化物薄膜制备",
        "tags": ["thin-film", "coating", "spin-coating"],
        "content": (
            "<p><strong>实验目的：</strong>采用旋涂法在【基底名称，如ITO玻璃/硅片】上制备【薄膜材料名称】薄膜，"
            "研究【变量，如转速/浓度/退火温度】对薄膜质量的影响。</p>"
            "<p><strong>材料与试剂：</strong></p><ul>"
            "<li>薄膜材料前驱体：【名称】，浓度【填写】mg/mL，溶剂【填写】</li>"
            "<li>基底：【名称】，尺寸【填写，如2×2 cm】</li>"
            "<li>其他试剂：【名称】，用途【填写】</li></ul>"
            "<p><strong>基底预处理：</strong></p><ul>"
            "<li>清洗方式：【超声清洗/UV臭氧处理/等离子清洗】</li>"
            "<li>清洗步骤：【填写】</li></ul>"
            "<p><strong>旋涂参数：</strong></p><ul>"
            "<li>预转速：【填写】rpm，时间：【填写】秒</li>"
            "<li>主转速：【填写】rpm，时间：【填写】秒</li>"
            "<li>旋涂次数：【填写】层</li>"
            "<li>每层中间处理：【退火/干燥】，温度【填写】°C，时间【填写】分钟</li></ul>"
            "<p><strong>后处理：</strong></p><ul>"
            "<li>退火温度：【填写】°C</li>"
            "<li>退火时间：【填写】分钟</li>"
            "<li>退火气氛：【空气/N2/真空】</li></ul>"
            "<p><strong>实验步骤：</strong></p><ol>"
            "<li>清洗基底并预处理</li>"
            "<li>配制前驱体溶液，搅拌至澄清，过滤（如需）</li>"
            "<li>将基底吸附在旋涂仪吸盘上</li>"
            "<li>滴加前驱体溶液覆盖基底表面</li>"
            "<li>启动旋涂程序：【预转参数】→【主转参数】</li>"
            "<li>（多层膜）重复滴加和旋涂</li>"
            "<li>退火处理</li></ol>"
            "<p><strong>表征计划：</strong>膜厚测量 / SEM截面 / AFM / UV-Vis透过率 / 【其他】</p>"
            "<p><strong>备注：</strong>【填写其他信息】</p>"
        ),
    },
    {
        "id": "ball-milling",
        "title": "球磨法制粉",
        "category": "合成",
        "description": "高能球磨法制备复合粉体或机械合金化的标准实验记录模板",
        "tags": ["ball-milling", "synthesis", "composite"],
        "content": (
            "<p><strong>实验目的：</strong>通过球磨法制备【目标粉体名称】复合粉体，研究【变量，如球磨时间/转速/球料比】"
            "对粉体【性能指标，如粒径/混合均匀性/相变】的影响。</p>"
            "<p><strong>材料：</strong></p><ul>"
            "<li>原料1：【名称】，用量【填写】g，纯度【填写】</li>"
            "<li>原料2：【名称】，用量【填写】g，纯度【填写】</li>"
            "<li>过程控制剂（PCA）：【名称，如乙醇/硬脂酸】，用量【填写】mL或wt%</li></ul>"
            "<p><strong>球磨参数：</strong></p><ul>"
            "<li>球磨罐材质：【不锈钢/氧化锆/玛瑙/碳化钨】</li>"
            "<li>球磨罐容积：【填写】mL</li>"
            "<li>磨球材质及尺寸：【填写，如Φ10mm氧化锆球】</li>"
            "<li>球料比：【填写，如10:1】</li>"
            "<li>转速：【填写】rpm</li>"
            "<li>球磨时间：【填写】小时</li>"
            "<li>球磨模式：【单向/双向交替】，停机间隔【填写】分钟</li>"
            "<li>气氛保护：【氩气/N2/空气】</li></ul>"
            "<p><strong>实验步骤：</strong></p><ol>"
            "<li>称取各原料粉末</li>"
            "<li>将磨球和原料按比例装入球磨罐</li>"
            "<li>加入过程控制剂</li>"
            "<li>（如需）充入保护气体</li>"
            "<li>密封球磨罐，安装到球磨机上</li>"
            "<li>设定参数，启动球磨</li>"
            "<li>球磨结束后，取出粉末，过筛（如需）</li></ol>"
            "<p><strong>表征计划：</strong>XRD / SEM / 粒径分析 / BET / 【其他】</p>"
            "<p><strong>备注：</strong>【填写其他信息，如罐体温度、出料情况等】</p>"
        ),
    },
    {
        "id": "electrochemistry",
        "title": "电化学性能测试",
        "category": "表征",
        "description": "电池/超级电容器/电催化材料的电化学测试标准实验记录模板",
        "tags": ["electrochemistry", "battery", "characterization"],
        "content": (
            "<p><strong>实验目的：</strong>测试【材料名称】的【测试类型，如充放电/循环伏安/阻抗/EIS】性能，"
            "评估其作为【应用场景，如锂电正极/超电电极/电催化剂】的电化学表现。</p>"
            "<p><strong>电极制备：</strong></p><ul>"
            "<li>活性物质：【名称】，质量【填写】mg</li>"
            "<li>导电剂：【名称，如Super P/乙炔黑】，质量【填写】mg，比例【填写】%</li>"
            "<li>粘结剂：【名称，如PVDF/PTFE】，质量【填写】mg，比例【填写】%</li>"
            "<li>溶剂：【名称，如NMP】，用量【填写】</li>"
            "<li>集流体：【铝箔/铜箔/碳纸】，尺寸【填写】</li>"
            "<li>活性物质负载量：【填写】mg/cm²</li></ul>"
            "<p><strong>电解液：</strong>【名称及浓度，如1M LiPF6 in EC:DMC=1:1】</p>"
            "<p><strong>测试条件：</strong></p><ul>"
            "<li>电池组装方式：【扣式电池CR2032/三电极体系/Swagelok】</li>"
            "<li>对电极/参比电极：【Li片/Ag/AgCl/Pt】</li>"
            "<li>隔膜：【名称，如Celgard 2400】</li>"
            "<li>电压窗口：【填写】~【填写】V</li>"
            "<li>测试温度：【填写】°C</li></ul>"
            "<p><strong>测试项目：</strong></p><ul>"
            "<li>循环伏安（CV）：扫速【填写】mV/s</li>"
            "<li>恒流充放电：电流密度【填写】mA/g 或 C倍率【填写】</li>"
            "<li>交流阻抗（EIS）：频率范围【填写】Hz ~ 【填写】Hz</li>"
            "<li>循环寿命：循环次数【填写】</li></ul>"
            "<p><strong>备注：</strong>【填写其他信息】</p>"
        ),
    },
    {
        "id": "xrd",
        "title": "XRD 物相表征",
        "category": "表征",
        "description": "X射线衍射分析实验记录模板，用于物相鉴定和晶体结构分析",
        "tags": ["XRD", "characterization"],
        "content": (
            "<p><strong>实验目的：</strong>利用X射线衍射（XRD）分析【样品名称】的物相组成、晶体结构和结晶度。</p>"
            "<p><strong>样品信息：</strong></p><ul>"
            "<li>样品编号：【填写】</li>"
            "<li>样品来源：【合成/购买/处理后】</li>"
            "<li>样品形态：【粉末/块体/薄膜】</li>"
            "<li>制样方式：【平铺法/压片法/原片直接测试】</li></ul>"
            "<p><strong>测试参数：</strong></p><ul>"
            "<li>仪器型号：【填写，如 Bruker D8 Advance】</li>"
            "<li>靶材：Cu Kα（λ=1.5406 Å）/ Co / Mo</li>"
            "<li>管电压：【填写】kV，管电流：【填写】mA</li>"
            "<li>扫描范围 2θ：【填写】° ~ 【填写】°</li>"
            "<li>扫描步长：【填写】°，每步时间：【填写】s</li>"
            "<li>扫描模式：连续扫描 / 步进扫描</li></ul>"
            "<p><strong>数据处理与分析：</strong></p><ul>"
            "<li>物相检索数据库：PDF-2 / ICSD / COD</li>"
            "<li>主要衍射峰归属：【填写】</li>"
            "<li>晶粒尺寸计算（Scherrer公式）：【填写】</li>"
            "<li>结晶度估算：【填写】</li></ul>"
            "<p><strong>结论：</strong>【填写物相分析结果】</p>"
            "<p><strong>备注：</strong>【填写其他信息】</p>"
        ),
    },
    {
        "id": "perovskite-solar",
        "title": "钙钛矿太阳能电池制备",
        "category": "器件",
        "description": "钙钛矿太阳能电池完整制备流程，含各层旋涂与蒸镀工艺记录",
        "tags": ["thin-film", "solar-cell", "coating", "perovskite"],
        "content": (
            "<p><strong>实验目的：</strong>制备结构为【填写，如FTO/ETL/Perovskite/HTL/Au】的钙钛矿太阳能电池，"
            "优化【变量，如钙钛矿组分/退火条件/界面修饰】，提升光电转换效率。</p>"
            "<p><strong>材料：</strong></p><ul>"
            "<li>基底：【FTO/ITO玻璃】，尺寸【填写，如2×2 cm】，方阻【填写】Ω/sq</li>"
            "<li>电子传输层（ETL）：【如SnO2/TiO2/PCBM】，前驱体【填写】</li>"
            "<li>钙钛矿前驱体：【如PbI2+MAI/FAI/CsI】，配比【填写】，溶剂【DMF/DMSO】，浓度【填写】M</li>"
            "<li>空穴传输层（HTL）：【如Spiro-OMeTAD/PTAA】，浓度【填写】mg/mL</li>"
            "<li>电极：Au/Ag/C，厚度【填写】nm</li></ul>"
            "<p><strong>实验步骤：</strong></p><ol>"
            "<li>基底清洗：洗涤剂超声→去离子水→丙酮→异丙醇，各【填写】分钟</li>"
            "<li>UV-臭氧/等离子体处理【填写】分钟</li>"
            "<li>旋涂 ETL：【转速】rpm，【时间】s → 退火【温度】°C，【时间】min</li>"
            "<li>（如需要）进入手套箱</li>"
            "<li>旋涂钙钛矿层：【转速】rpm，【时间】s，滴加反溶剂【名称，如氯苯】，【时间】s</li>"
            "<li>退火：【温度】°C，【时间】min</li>"
            "<li>旋涂 HTL：【转速】rpm，【时间】s</li>"
            "<li>（如需）氧化处理：空气中放置【时间】h 或 O2 plasma</li>"
            "<li>蒸镀电极：厚度【填写】nm，速率【填写】Å/s</li></ol>"
            "<p><strong>器件性能测试（JV曲线）：</strong></p><ul>"
            "<li>光源：AM 1.5G，100 mW/cm²</li>"
            "<li>扫描方向：正向/反向</li>"
            "<li>有效面积：【填写】cm²</li>"
            "<li>Voc：【填写】V，Jsc：【填写】mA/cm²，FF：【填写】%，PCE：【填写】%</li></ul>"
            "<p><strong>表征计划：</strong>SEM截面 / XRD / PL / TRPL / UV-Vis吸收 / IPCE</p>"
            "<p><strong>备注：</strong>【填写其他信息】</p>"
        ),
    },
]


template_store = TemplateStore(str(BASE_DIR / "experiment_templates"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max request body
store = ExperimentStore(str(BASE_DIR / "experiments"))
favorites_store = FavoritesStore(str(BASE_DIR / "experiments" / "_favorites.yaml"))
analysis_store = AnalysisStore(str(BASE_DIR / "experiments" / "_analysis_history"))
update_log_store = UpdateLogStore(str(BASE_DIR / "experiments" / "_update_logs"))
thread_store = ThreadStore(str(BASE_DIR / "experiments" / "_threads"))


def get_extract_llm():
    """LLM for structured extraction (needs function calling support)."""
    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None
    model = config.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    return LLMClient(api_key=api_key, model=model)


def get_analyze_llm():
    """LLM for cross-experiment analysis (reasoning preferred)."""
    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None
    model = config.get("DEEPSEEK_ANALYZE_MODEL", config.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))
    return LLMClient(api_key=api_key, model=model)


def get_agent_llm():
    """LLM for conversational agent (flash model for speed/cost)."""
    api_key = config.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None
    model = config.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    return LLMClient(api_key=api_key, model=model)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    experiments = store.list_all()
    pinned_ids = favorites_store.get_pinned()
    # Separate pinned experiments (keep order) and the rest
    pinned = []
    others = []
    for exp in experiments:
        if exp["id"] in pinned_ids:
            pinned.append(exp)
        else:
            others.append(exp)
    # Sort pinned by pin order
    pinned.sort(key=lambda e: pinned_ids.index(e["id"]) if e["id"] in pinned_ids else 99)
    # Merge: pinned first, then others
    sorted_experiments = pinned + others
    return render_template("index.html", experiments=sorted_experiments,
                           pinned_ids=pinned_ids)


# ---------------------------------------------------------------------------
# New experiment form
# ---------------------------------------------------------------------------
@app.route("/new")
def new_experiment():
    return render_template("new.html")


# ---------------------------------------------------------------------------
# Parse natural language notes -> structured experiment
# ---------------------------------------------------------------------------
@app.route("/api/parse", methods=["POST"])
def api_parse():
    notes_raw = request.form.get("notes", "").strip()
    # Support JSON body (for AJAX preview flow)
    if request.is_json:
        notes_raw = request.json.get("notes", "").strip()
    is_json = request.is_json or request.headers.get("Accept", "") == "application/json"

    if notes_raw and "<" in notes_raw:
        notes_plain = strip_html(notes_raw)
    else:
        notes_plain = notes_raw
    if not notes_plain or len(notes_plain) < 10:
        msg = "实验描述太短，请提供更多细节（至少 10 个字符）。"
        if is_json:
            return jsonify({"ok": False, "error": msg}), 400
        return render_template("new.html", error=msg)

    llm = get_extract_llm()
    if not llm:
        msg = '未配置 DeepSeek API Key。请点击导航栏的"设置"按钮配置 API Key。'
        if is_json:
            return jsonify({"ok": False, "error": msg}), 500
        return render_template("new.html", error=msg)

    try:
        result = parse_notes(notes_plain, llm)
    except Exception as e:
        msg = f"AI 处理失败: {str(e)}"
        if is_json:
            return jsonify({"ok": False, "error": msg}), 500
        return render_template("new.html", error=msg)

    result["original_notes"] = notes_raw if notes_raw else notes_plain
    result["id"] = store.next_id()
    if is_json:
        return jsonify({"ok": True, "data": result})
    # Legacy form-submit fallback: save directly and redirect
    exp_id = store.save(result)
    _move_draft_images(exp_id)
    return redirect(url_for("view_experiment", exp_id=exp_id))


def _move_draft_images(exp_id: str):
    draft_dir = BASE_DIR / "uploads" / "_draft"
    if draft_dir.exists():
        exp_img_dir = BASE_DIR / "uploads" / exp_id
        exp_img_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        for f in draft_dir.iterdir():
            shutil.move(str(f), str(exp_img_dir / f.name))
        draft_dir.rmdir()


@app.route("/api/parse/confirm", methods=["POST"])
def api_parse_confirm():
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "无效的实验数据"}), 400

    exp_id = data.get("id", store.next_id())
    # Clean up None values and empty strings from the editable fields
    data["id"] = exp_id
    # Parse references from original_notes
    notes = data.get("original_notes", "")
    refs = _extract_references(notes)
    data["references"] = refs
    store.save(data)
    _update_referenced_by(exp_id, refs)
    _move_draft_images(exp_id)
    return jsonify({"ok": True, "exp_id": exp_id})


def _extract_references(text: str) -> list[str]:
    import re
    pattern = r"@(EXP-\d{4}-\d{3})"
    seen = set()
    refs = []
    for m in re.finditer(pattern, text):
        rid = m.group(1)
        if rid not in seen:
            seen.add(rid)
            refs.append(rid)
    return refs


def _update_referenced_by(exp_id: str, refs: list[str]):
    """Update referenced_by on experiments that this experiment references."""
    for ref_id in refs:
        ref_exp = store.load(ref_id)
        if ref_exp:
            rb = ref_exp.get("referenced_by", [])
            if exp_id not in rb:
                rb.append(exp_id)
                ref_exp["referenced_by"] = rb
                store.save(ref_exp)


def _compute_diff(old: dict, new: dict) -> list[dict]:
    """Compare two experiment dicts and return a list of {path, field, old, new} changes.
    Only includes fields where the value actually changed."""
    changes = []
    simple_fields = ["title", "date", "experimenter", "status", "purpose",
                     "conclusion", "original_notes"]
    array_fields = ["tags", "sop", "next_steps"]
    complex_fields = ["materials", "equipment", "experimental_plan",
                      "process_parameters", "characterization"]
    nested_fields = ["observations", "results"]

    for field in simple_fields:
        old_val = (old.get(field) or "") if old else ""
        new_val = (new.get(field) or "") if new else ""
        if old_val != new_val:
            changes.append({
                "path": field,
                "field": field,
                "old": str(old_val)[:200],
                "new": str(new_val)[:200],
            })

    for field in array_fields:
        old_val = old.get(field, []) if old else []
        new_val = new.get(field, []) if new else []
        if old_val != new_val:
            changes.append({
                "path": field,
                "field": field,
                "old": ", ".join(str(v) for v in old_val)[:200],
                "new": ", ".join(str(v) for v in new_val)[:200],
            })

    for field in complex_fields:
        old_items = old.get(field, []) if old else []
        new_items = new.get(field, []) if new else []
        if old_items != new_items:
            # Compare by serializing to JSON for complex nested structures
            import json as _json
            if _json.dumps(old_items, ensure_ascii=False, sort_keys=True) != \
               _json.dumps(new_items, ensure_ascii=False, sort_keys=True):
                changes.append({
                    "path": field,
                    "field": field,
                    "old": f"{len(old_items)} entries" if old_items else "empty",
                    "new": f"{len(new_items)} entries" if new_items else "empty",
                })

    for field in nested_fields:
        old_val = old.get(field, {}) if old else {}
        new_val = new.get(field, {}) if new else {}
        if old_val != new_val:
            changes.append({
                "path": field,
                "field": field,
                "old": "filled" if old_val else "empty",
                "new": "filled" if new_val else "empty",
            })

    return changes


def _log_update(exp_id: str, source: str, old_exp: dict | None,
                new_exp: dict, thread_id: str | None = None) -> str | None:
    """Compute diff and write update log entry. Returns entry_id or None if no changes."""
    changes = _compute_diff(old_exp, new_exp)
    if not changes:
        return None
    return update_log_store.append(
        exp_id=exp_id,
        source=source,
        changes=changes,
        context={"summary": f"修改了 {len(changes)} 个字段"},
        thread_id=thread_id,
    )


# ---------------------------------------------------------------------------
# View experiment
# ---------------------------------------------------------------------------
@app.route("/experiments/<exp_id>")
def view_experiment(exp_id):
    exp = store.load(exp_id)
    if not exp:
        return "Experiment not found", 404
    return render_template("view.html", exp=exp)


# ---------------------------------------------------------------------------
# Raw YAML view
# ---------------------------------------------------------------------------
@app.route("/experiments/<exp_id>/yaml")
def view_yaml(exp_id):
    exp = store.load(exp_id)
    if not exp:
        return "Experiment not found", 404
    raw = yaml.dump(exp, allow_unicode=True, sort_keys=False,
                    default_flow_style=False, indent=2)
    return raw, 200, {"Content-Type": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------------------
# Edit experiment (raw YAML)
# ---------------------------------------------------------------------------
@app.route("/experiments/<exp_id>/edit", methods=["GET", "POST"])
def edit_experiment(exp_id):
    if request.method == "GET":
        exp = store.load(exp_id)
        if not exp:
            return "Experiment not found", 404
        raw = yaml.dump(exp, allow_unicode=True, sort_keys=False,
                        default_flow_style=False, indent=2)
        return render_template("edit.html", exp_id=exp_id, yaml_raw=raw)

    # POST: save edited YAML
    yaml_text = request.form.get("yaml_content", "")
    try:
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            raise ValueError("YAML content must be a dictionary")
        # Read old values from disk before update (for diff)
        old_exp = store.load(exp_id)
        store.update(exp_id, data)
        _log_update(exp_id, "manual_edit", old_exp, data)
        return redirect(url_for("view_experiment", exp_id=exp_id))
    except Exception as e:
        return render_template("edit.html", exp_id=exp_id,
                               yaml_raw=yaml_text,
                               error=f"YAML 解析失败: {str(e)}")


# ---------------------------------------------------------------------------
# Delete experiment
# ---------------------------------------------------------------------------
@app.route("/experiments/<exp_id>/delete", methods=["DELETE"])
def delete_experiment(exp_id):
    # Write system log before deleting
    update_log_store.append(
        exp_id=exp_id,
        source="system",
        changes=[{"path": "_deleted", "field": "实验记录",
                  "old": exp_id, "new": "[已删除]"}],
        context={"summary": f"实验记录 {exp_id} 已被删除"},
    )
    store.delete(exp_id)
    return "", 200


# ---------------------------------------------------------------------------
# Save edited experiment (JSON from inline editing)
# ---------------------------------------------------------------------------
@app.route("/experiments/<exp_id>/save-json", methods=["POST"])
def save_experiment_json(exp_id):
    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "Invalid JSON"}), 400
    # Read old values from disk before update (for diff)
    old_exp = store.load(exp_id)
    # Extract and update references
    notes = data.get("original_notes", "")
    refs = _extract_references(notes)
    old_refs = old_exp.get("references", []) if old_exp else []
    data["references"] = refs
    store.update(exp_id, data)
    _update_referenced_by(exp_id, refs)
    # Write update log
    _log_update(exp_id, "manual_edit", old_exp, data)
    # Remove from old references
    for rid in old_refs:
        if rid not in refs:
            r_exp = store.load(rid)
            if r_exp:
                rb = r_exp.get("referenced_by", [])
                if exp_id in rb:
                    rb.remove(exp_id)
                    r_exp["referenced_by"] = rb
                    store.save(r_exp)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Re-generate experiment from modified original notes
# ---------------------------------------------------------------------------
@app.route("/experiments/<exp_id>/regenerate", methods=["POST"])
def regenerate_experiment(exp_id):
    exp = store.load(exp_id)
    if not exp:
        return jsonify({"ok": False, "error": "Experiment not found"}), 404

    notes_raw = request.form.get("original_notes", "").strip()
    # Strip HTML if present (from Quill editor)
    if notes_raw and "<" in notes_raw:
        notes_plain = strip_html(notes_raw)
    else:
        notes_plain = notes_raw
    if not notes_plain or len(notes_plain) < 10:
        return jsonify({"ok": False, "error": "Notes too short"}), 400

    llm = get_extract_llm()
    if not llm:
        return jsonify({"ok": False, "error": "No API key configured"}), 500

    try:
        result = parse_notes(notes_plain, llm)
    except Exception as e:
        return jsonify({"ok": False, "error": f"AI processing failed: {str(e)}"}), 500

    # Keep the rich HTML version if that's what was submitted
    result["original_notes"] = notes_raw if notes_raw else notes_plain
    result["id"] = exp_id
    refs = _extract_references(notes_raw if notes_raw else notes_plain)
    result["references"] = refs
    old_exp = store.load(exp_id)
    old_refs = old_exp.get("references", []) if old_exp else []
    store.update(exp_id, result)
    _update_referenced_by(exp_id, refs)
    for rid in old_refs:
        if rid not in refs:
            r_exp = store.load(rid)
            if r_exp:
                rb = r_exp.get("referenced_by", [])
                if exp_id in rb:
                    rb.remove(exp_id)
                    r_exp["referenced_by"] = rb
                    store.save(r_exp)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Print-friendly view for PDF export (browser Ctrl+P -> Save as PDF)
# ---------------------------------------------------------------------------
@app.route("/experiments/<exp_id>/print")
def print_experiment(exp_id):
    exp = store.load(exp_id)
    if not exp:
        return "Experiment not found", 404
    return render_template("print.html", exp=exp)


# ---------------------------------------------------------------------------
# Timeline view
# ---------------------------------------------------------------------------
@app.route("/timeline")
def timeline():
    experiments = store.list_all_full()
    experiments.sort(key=lambda e: e.get("date") or "")
    return render_template("timeline.html", experiments=experiments)


# ---------------------------------------------------------------------------
# Analysis page
# ---------------------------------------------------------------------------
@app.route("/analyze")
def analyze_page():
    count = store.count()
    return render_template("analyze.html", count=count)


# ---------------------------------------------------------------------------
# Run analysis
# ---------------------------------------------------------------------------
@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    question = request.form.get("question", "").strip()
    selected_raw = request.form.get("selected_ids", "")
    selected_ids = [s.strip() for s in selected_raw.split(",") if s.strip()] if selected_raw else []
    count = store.count()

    if count == 0:
        return render_template("analyze.html", count=0,
                               error="还没有实验记录，请先创建一些实验。")

    if not selected_ids:
        return render_template("analyze.html", count=count,
                               error="请至少选择一个实验进行分析。")

    llm = get_analyze_llm()
    if not llm:
        return render_template("analyze.html", count=count,
                               error="未配置 DeepSeek API Key。请点击导航栏的设置按钮配置 API Key。")

    summary = store.summarize_all(exp_ids=selected_ids)
    if not question:
        question = "请对你的所有实验进行全盘分析，找出趋势、矛盾和下一步方向。"

    try:
        analysis = analyze_experiments(summary, question, llm)
        # Auto-save analysis
        analysis_data = {
            "timestamp": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "question": question,
            "selected_ids": selected_ids,
            "analysis": analysis,
        }
        analysis_id = analysis_store.save(analysis_data)
        return render_template("analyze.html", analysis=analysis, count=count,
                               analysis_id=analysis_id)
    except Exception as e:
        return render_template("analyze.html", count=count,
                               error=f"分析失败: {str(e)}")


@app.route("/api/analysis-history")
def api_analysis_history():
    return jsonify(analysis_store.list_all())


@app.route("/api/analysis-history/<anal_id>")
def api_analysis_detail(anal_id):
    a = analysis_store.load(anal_id)
    if not a:
        return jsonify({"ok": False, "error": "Not found"}), 404
    return jsonify({"ok": True, "data": a})


@app.route("/api/analysis-history/<anal_id>", methods=["DELETE"])
def api_analysis_delete(anal_id):
    ok = analysis_store.delete(anal_id)
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# Template library
# ---------------------------------------------------------------------------
@app.route("/templates")
def template_library():
    templates = template_store.list_all()
    return render_template("templates.html", templates=templates)


@app.route("/api/templates/<template_id>")
def api_get_template(template_id):
    tmpl = template_store.load(template_id)
    if not tmpl:
        return jsonify({"ok": False, "error": "Template not found"}), 404
    return jsonify({
        "ok": True,
        "title": tmpl.get("title", ""),
        "content": tmpl.get("content", ""),
    })


# ---------------------------------------------------------------------------
# Search API — return all experiments with full data for client-side filtering
# ---------------------------------------------------------------------------
@app.route("/api/experiments/search")
def api_experiments_search():
    return jsonify(store.list_all_full())


# ---------------------------------------------------------------------------
# Compare view
# ---------------------------------------------------------------------------
@app.route("/compare")
def compare_experiments():
    ids_raw = request.args.get("ids", "")
    ids = [s.strip() for s in ids_raw.split(",") if s.strip()]
    if len(ids) < 2:
        return redirect(url_for("index"))
    experiments = []
    for eid in ids[:4]:  # max 4
        exp = store.load(eid)
        if exp:
            experiments.append(exp)
    if len(experiments) < 2:
        return redirect(url_for("index"))
    # Assign colors
    colors = ["#2563eb", "#dc2626", "#16a34a", "#9333ea"]  # blue, red, green, purple
    for i, exp in enumerate(experiments):
        exp["_color"] = colors[i % len(colors)]
    return render_template("compare.html", experiments=experiments, color_names=["蓝", "红", "绿", "紫"])


# ---------------------------------------------------------------------------
# Agent API v2 — conversational experiment recording (tool-calling)
# ---------------------------------------------------------------------------

@app.route("/api/agent/start", methods=["POST"])
def api_agent_start():
    """初始化 Agent 对话。如有 _current_state.yaml → 恢复；如无 → 全新。"""
    llm = get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    # 尝试从 _current_state.yaml 恢复
    saved = thread_store.load_current_state()
    if saved:
        agent = AgentLoop.from_dict(llm, store, saved,
                                    thread_store=thread_store,
                                    update_log_store=update_log_store,
                                    favorites_store=favorites_store,
                                    analysis_store=analysis_store)
        return jsonify({
            "ok": True,
            "state": agent.state_to_dict(),
            "type": "reply",
            "message": None,
            "greeting": None,
        })

    # 全新初始化
    agent = AgentLoop(llm, store, thread_store=thread_store,
                     update_log_store=update_log_store,
                     favorites_store=favorites_store,
                     analysis_store=analysis_store)
    result = agent.run("")
    return jsonify({
        "ok": True,
        "state": agent.state_to_dict(),
        "type": result["type"],
        "message": result.get("message", ""),
        "greeting": result.get("message", ""),
        "context": result.get("context", {}),
    })


@app.route("/api/agent/message", methods=["POST"])
def api_agent_message():
    """处理用户消息，返回 Agent 回复。检测到提取信号时自动执行提取。"""
    llm = get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"ok": False, "error": "缺少请求数据"}), 400

    user_message = (data.get("message") or "").strip()
    state_dict = data.get("state")
    if not user_message:
        return jsonify({"ok": False, "error": "消息不能为空"}), 400
    if not state_dict:
        return jsonify({"ok": False, "error": "缺少 state"}), 400

    # 从 state dict 重建 AgentLoop
    agent = AgentLoop.from_dict(llm, store, state_dict,
                                thread_store=thread_store,
                                update_log_store=update_log_store,
                                favorites_store=favorites_store,
                                analysis_store=analysis_store)
    result = agent.run(user_message)

    # 父Agent generate_record → 自动保存，无需预览
    if result["type"] in ("extract", "generate"):
        notes = result.get("notes") or _build_notes_from_context(result.get("context", {}))
        preview = result.get("preview") or _extract_or_fallback(notes, result.get("context", {}), agent)
        preview["id"] = store.next_id()
        refs = _extract_references(notes)
        preview["references"] = refs
        store.save(preview)
        _update_referenced_by(preview["id"], refs)
        _move_draft_images(preview["id"])
        return jsonify({
            "ok": True,
            "type": "saved",
            "exp_id": preview["id"],
            "state": result.get("state") or agent.state_to_dict(),
            "message": result.get("message", "实验记录已生成。"),
        })

    return jsonify({
        "ok": True,
        "state": agent.state_to_dict(),
        "type": result["type"],
        "message": result.get("message", ""),
        "context": result.get("context", {}),
    })


@app.route("/api/agent/confirm", methods=["POST"])
def api_agent_confirm():
    """确认保存 Agent 生成的实验记录。委托到现有保存逻辑。"""
    return api_parse_confirm()


# ---------------------------------------------------------------------------
# Child Agent API — EXP 详情页对话修改
# ---------------------------------------------------------------------------

@app.route("/api/exp/<exp_id>/chat", methods=["POST"])
def api_exp_chat(exp_id):
    """子 Agent 入口。空消息=打开面板(只加载上下文不跑LLM)；有消息=发送消息(正常运行)。"""
    llm = get_agent_llm()
    if not llm:
        return jsonify({"ok": False, "error": "未配置 DeepSeek API Key"}), 500

    data = request.get_json() or {}
    user_message = (data.get("message") or "").strip()
    state_dict = data.get("state")
    is_legacy = data.get("is_legacy", False)

    # 查反向映射
    idx = thread_store.get_index()
    thread_id = idx.get("exp_to_thread", {}).get(exp_id)

    # ---- 旧实验（无线程）----
    if not thread_id:
        exp = store.load(exp_id)
        if not exp:
            return jsonify({"ok": False, "error": "实验不存在"}), 404

        # 尝试从磁盘恢复永久状态（legacy用exp_id作为key）
        disk_state = thread_store.load_child_state(exp_id)
        if disk_state and not is_legacy:
            agent = AgentLoop.from_dict(llm, store, disk_state,
                                        thread_store=thread_store,
                                        update_log_store=update_log_store,
                                        favorites_store=favorites_store,
                                        analysis_store=analysis_store)
            if user_message:
                result = agent.run(user_message)
                return _make_chat_response(agent, result, None, thread_store)
            else:
                state = agent.state_to_dict()
                thread_store.save_child_state(exp_id, state)
                return jsonify({"ok": True, "state": state})

        # 仅打开面板 → 返回实验数据让前端展示legacy警告
        if not user_message and not is_legacy:
            return jsonify({
                "ok": True, "is_legacy": True,
                "exp_data": {
                    "id": exp.get("id"), "title": exp.get("title", ""),
                    "date": exp.get("date", ""), "status": exp.get("status", ""),
                    "tags": exp.get("tags", []),
                    "purpose": (exp.get("purpose") or "")[:200],
                    "materials": exp.get("materials", []),
                    "sop": exp.get("sop", []),
                    "process_parameters": exp.get("process_parameters", []),
                    "results": exp.get("results", {}),
                    "conclusion": (exp.get("conclusion") or "")[:200],
                    "next_steps": exp.get("next_steps", []),
                },
            })
        # 旧实验 + 消息 → 创建 legacy child agent 并运行
        exp_data = {
            "id": exp.get("id"), "title": exp.get("title", ""),
            "tags": exp.get("tags", []),
            "purpose": (exp.get("purpose") or "")[:200],
            "materials": exp.get("materials", []),
            "sop": exp.get("sop", []),
            "process_parameters": exp.get("process_parameters", []),
            "results": exp.get("results", {}),
            "conclusion": (exp.get("conclusion") or "")[:200],
            "next_steps": exp.get("next_steps", []),
            "status": exp.get("status", "done"),
            "date": exp.get("date", ""),
            "experimenter": exp.get("experimenter", ""),
        }
        agent = AgentLoop.create_legacy_child_agent(
            llm, store, exp_data,
            thread_store=thread_store, update_log_store=update_log_store,
            favorites_store=favorites_store, analysis_store=analysis_store)
        agent._child_exp_id = exp_id
        agent.history.append({
            "role": "system",
            "content": f"[修改模式] 你正在修改已完成的实验 {exp_id}。修改前先用 load_reference 加载磁盘最新数据（不要依赖对话记忆）。修改用 modify_experiment 工具直接执行，会自动保存和记录日志。不要用 update_schema 或 generate_record。查询信息用 query_experiment，查历史用 read_update_log。"
        })
        result = agent.run(user_message)
        return _make_chat_response(agent, result, thread_id, thread_store)

    # ---- 有线程（正常实验）----

    # 恢复已有子Agent状态（前端 sessionStorage 或磁盘 child_state.yaml）
    if not state_dict:
        # 尝试从磁盘恢复永久状态
        disk_state = thread_store.load_child_state(thread_id)
        if disk_state:
            state_dict = disk_state

    if state_dict:
        agent = AgentLoop.from_dict(llm, store, state_dict,
                                    thread_store=thread_store,
                                    update_log_store=update_log_store,
                                    favorites_store=favorites_store,
                                    analysis_store=analysis_store)
        if user_message:
            result = agent.run(user_message)
            return _make_chat_response(agent, result, thread_id, thread_store)
        else:
            # 仅恢复状态，不跑LLM
            state = agent.state_to_dict()
            if thread_id:
                thread_store.save_child_state(thread_id, state)
            return jsonify({"ok": True, "state": state})

    # 首次打开 → 加载线程上下文，不跑LLM
    parent = AgentLoop(llm, store, thread_store=thread_store,
                      update_log_store=update_log_store,
                      favorites_store=favorites_store,
                      analysis_store=analysis_store)
    agent = AgentLoop.create_child_agent(parent, thread_id)
    agent._child_exp_id = exp_id
    agent.history.append({
        "role": "system",
        "content": f"[修改模式] 你正在修改已完成的实验 {exp_id}。修改前先用 load_reference 加载磁盘最新数据（不要依赖对话记忆）。修改用 modify_experiment 工具直接执行，会自动保存和记录日志。不要用 update_schema 或 generate_record。查询信息用 query_experiment，查历史用 read_update_log。"
    })

    if user_message:
        result = agent.run(user_message)
        return _make_chat_response(agent, result, thread_id, thread_store)
    else:
        # 无消息 → 仅返回状态，不跑LLM
        state = agent.state_to_dict()
        if thread_id:
            thread_store.save_child_state(thread_id, state)
        return jsonify({"ok": True, "state": state})


def _make_chat_response(agent, result, thread_id, thread_store):
    """构造子Agent的HTTP响应。"""
    state = agent.state_to_dict()
    # 子Agent状态持久化到磁盘（永久保留，不随确认保存而删除）
    key = thread_id or agent._child_exp_id
    if key:
        thread_store.save_child_state(key, state)

    if result["type"] in ("extract", "generate"):
        preview = agent._generated_preview
        return jsonify({
            "ok": True, "type": "extract", "state": state,
            "message": result.get("message", "实验记录已生成，请在预览中确认。"),
            "preview": preview,
        })
    return jsonify({
        "ok": True, "state": state,
        "type": result["type"],
        "message": result.get("message", ""),
    })


@app.route("/api/exp/<exp_id>/confirm", methods=["POST"])
def api_exp_confirm(exp_id):
    """子 Agent 确认保存。读磁盘旧值 → 写更新日志 → 保存 → 子Agent清理。"""
    body = request.get_json()
    if not body or not isinstance(body, dict):
        return jsonify({"ok": False, "error": "无效的请求数据"}), 400

    # 从 body 中提取实验数据和 state
    data = body.get("preview") or {}
    state_dict = body.get("state")
    if not data or not isinstance(data, dict):
        return jsonify({"ok": False, "error": "缺少实验数据"}), 400

    # 读磁盘旧值
    old_exp = store.load(exp_id)
    data["id"] = exp_id

    # 整理引用
    notes = data.get("original_notes", "")
    refs = _extract_references(notes)
    old_refs = old_exp.get("references", []) if old_exp else []
    data["references"] = refs

    # 从 state 中获取 thread_id（如果有）
    thread_id = None
    if state_dict and isinstance(state_dict, dict):
        thread_id = state_dict.get("thread_id")

    # 写更新日志
    _log_update(exp_id, "child_agent", old_exp, data,
                thread_id=thread_id)

    # 保存
    store.save(data)
    # 统一日志：操作记录
    log = get_logger()
    if log:
        log.operation("exp_saved", agent="child", exp=exp_id, source="child_agent")
    _update_referenced_by(exp_id, refs)
    for rid in old_refs:
        if rid not in refs:
            r_exp = store.load(rid)
            if r_exp:
                rb = r_exp.get("referenced_by", [])
                if exp_id in rb:
                    rb.remove(exp_id)
                    r_exp["referenced_by"] = rb
                    store.save(r_exp)

    return jsonify({"ok": True, "exp_id": exp_id})



# -- 辅助函数 --

def _build_notes_from_context(context: dict) -> str:
    """从 context 生成自然语言实验描述（Python 模板，不调 LLM）。"""
    parts = []
    if context.get("title"):
        parts.append(f"实验标题: {context['title']}")
    if context.get("purpose"):
        parts.append(f"实验目的: {context['purpose']}")
    materials = context.get("materials", [])
    if materials:
        lines = ["材料与试剂:"]
        for m in materials:
            if isinstance(m, dict):
                name = m.get("name", "")
                purity = f", 纯度 {m['purity']}" if m.get("purity") else ""
                vendor = f", {m['vendor']}" if m.get("vendor") else ""
                amount = f", {m['amount']}" if m.get("amount") else ""
                lines.append(f"  - {name}{purity}{vendor}{amount}")
        parts.append("\n".join(lines))
    sop = context.get("sop", [])
    if sop:
        lines = ["实验步骤:"]
        for i, s in enumerate(sop, 1):
            lines.append(f"  {i}. {s}")
        parts.append("\n".join(lines))
    params = context.get("process_parameters", [])
    if params:
        lines = ["过程参数:"]
        for p in params:
            if isinstance(p, dict):
                lines.append(f"  - {p.get('parameter', '')}: {p.get('setpoint', '')}")
        parts.append("\n".join(lines))
    results = context.get("results", {})
    if isinstance(results, dict):
        if results.get("qualitative"):
            parts.append(f"定性结果: {results['qualitative']}")
        kd = results.get("key_data", [])
        if kd:
            lines = ["关键数据:"]
            for k in kd:
                if isinstance(k, dict):
                    lines.append(f"  - {k.get('metric', '')}: {k.get('value', '')}")
            parts.append("\n".join(lines))
    if context.get("conclusion"):
        parts.append(f"结论: {context['conclusion']}")
    if context.get("next_steps"):
        parts.append("下一步: " + "; ".join(str(s) for s in context["next_steps"]))
    return "\n\n".join(parts) if parts else "（无实验描述）"


def _extract_or_fallback(notes: str, context: dict, agent: AgentLoop) -> dict:
    """尝试 LLM 提取 → 失败则从 context 确定性构造预览数据。"""
    extract_llm = get_extract_llm()
    if extract_llm:
        try:
            result = parse_notes(notes, extract_llm)
            result["original_notes"] = notes
            result["id"] = store.next_id()
            result["references"] = list(agent.references)
            return result
        except Exception:
            pass

    # 回退：从 context 确定性构造
    return {
        "id": store.next_id(),
        "title": context.get("title", ""),
        "purpose": context.get("purpose", ""),
        "materials": context.get("materials", []),
        "sop": context.get("sop", []),
        "process_parameters": context.get("process_parameters", []),
        "observations": context.get("observations", {"no_anomalies": True, "items": []}),
        "results": context.get("results", {}),
        "conclusion": context.get("conclusion", ""),
        "next_steps": context.get("next_steps", []),
        "tags": context.get("tags", []),
        "status": context.get("status", "planned"),
        "original_notes": notes,
        "references": list(agent.references),
    }


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------
@app.route("/api/resolve-reference", methods=["POST"])
def api_resolve_reference():
    data = request.get_json()
    text = (data.get("text") or "").strip()
    if not text or len(text) < 2:
        return jsonify({"ok": False, "results": []})

    # Direct EXP-xxx match
    import re
    m = re.match(r"^(?:@)?(EXP-\d{4}-\d{3})$", text, re.IGNORECASE)
    if m:
        exp = store.load(m.group(1).upper())
        if exp:
            return jsonify({"ok": True, "results": [{
                "id": exp.get("id"),
                "title": exp.get("title", ""),
                "date": exp.get("date", ""),
                "tags": exp.get("tags", []),
                "score": 1.0
            }]})

    # Local fuzzy matching
    all_exps = store.list_all_full()
    results = []
    text_lower = text.lower()
    for exp in all_exps:
        score = 0.0
        title = (exp.get("title") or "").lower()
        tags = " ".join(exp.get("tags") or []).lower()
        purpose = (exp.get("purpose") or "")[:200].lower()
        materials = " ".join(m.get("name", "") for m in (exp.get("materials") or [])).lower()
        searchable = f"{title} {tags} {purpose} {materials}"

        # Each matching keyword adds to score
        keywords = text_lower.split()
        for kw in keywords:
            if kw in searchable:
                score += 0.2
            # Partial match
            if len(kw) >= 2:
                if kw in searchable:
                    score += 0.1

        # Bonus for tag matches
        for tag in (exp.get("tags") or []):
            if tag.lower() in text_lower:
                score += 0.3

        if score > 0:
            results.append({
                "id": exp.get("id"),
                "title": exp.get("title", ""),
                "date": exp.get("date", ""),
                "tags": exp.get("tags", []),
                "score": min(score, 0.99)
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    top = results[:5]

    # If no good local matches and text looks semantic (not an ID), try LLM
    if (not top or top[0]["score"] < 0.3) and not re.match(r"^EXP-", text, re.IGNORECASE):
        llm = get_extract_llm()
        if llm:
            try:
                import json
                exp_list = json.dumps([{
                    "id": e["id"],
                    "title": e.get("title", ""),
                    "tags": e.get("tags", []),
                } for e in all_exps], ensure_ascii=False)
                llm_result = llm.analyze(
                    system_prompt="你是实验记录搜索引擎。根据用户对历史实验的模糊描述，从实验列表中找出最匹配的。返回 JSON 数组：只包含 id 字段，按匹配度降序排列，最多返回5个。只返回JSON数组，不要其他文字。",
                    user_prompt=f"实验列表：\n{exp_list}\n\n用户描述：{text}\n\n请返回最匹配的实验ID列表(JSON数组):",
                    temperature=0.1
                )
                # Parse the JSON from the response
                try:
                    parsed = json.loads(llm_result.strip())
                    ai_ids = [item["id"] for item in parsed if "id" in item] if isinstance(parsed, list) else []
                    ai_results = []
                    for aid in ai_ids[:5]:
                        e = store.load(aid)
                        if e:
                            ai_results.append({
                                "id": e.get("id"),
                                "title": e.get("title", ""),
                                "date": e.get("date", ""),
                                "tags": e.get("tags", []),
                                "score": 0.85
                            })
                    if ai_results:
                        results = ai_results
                    else:
                        results = top
                except json.JSONDecodeError:
                    results = top
            except Exception:
                results = top

    return jsonify({"ok": True, "results": results[:5]})


# ---------------------------------------------------------------------------
# Favorites & Pinning
# ---------------------------------------------------------------------------
@app.route("/api/experiments/<exp_id>/pin", methods=["POST"])
def api_toggle_pin(exp_id):
    return jsonify(favorites_store.toggle_pin(exp_id))


@app.route("/api/experiments/<exp_id>/favorite", methods=["POST"])
def api_toggle_favorite(exp_id):
    collection = request.json.get("collection", "默认收藏夹") if request.json else "默认收藏夹"
    return jsonify(favorites_store.toggle_favorite(exp_id, collection))


@app.route("/api/favorites")
def favorites_page():
    collections = favorites_store.get_collections()
    return render_template("favorites.html", collections=collections)


@app.route("/api/list-collections")
def api_list_collections():
    return jsonify(favorites_store.get_collections())


@app.route("/api/collections", methods=["POST"])
def api_create_collection():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"ok": False, "error": "收藏夹名称不能为空"}), 400
    return jsonify(favorites_store.create_collection(data["name"].strip()))


@app.route("/api/collections/<name>", methods=["DELETE"])
def api_delete_collection(name):
    return jsonify(favorites_store.delete_collection(name))


# ---------------------------------------------------------------------------
# Image upload (for Quill rich text editor)
# ---------------------------------------------------------------------------
@app.route("/api/upload-image", methods=["POST"])
def api_upload_image():
    exp_id = request.form.get("exp_id", "_draft")
    file = request.files.get("image")
    if not file:
        return jsonify({"ok": False, "error": "No image"}), 400

    upload_dir = BASE_DIR / "uploads" / exp_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(file.filename).suffix if file.filename else ".png"
    if ext.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        ext = ".png"
    filename = f"{uuid.uuid4().hex[:8]}{ext}"
    filepath = upload_dir / filename
    file.save(str(filepath))

    url = f"/uploads/{exp_id}/{filename}"
    return jsonify({"ok": True, "url": url})


@app.route("/uploads/<path:filepath>")
def serve_upload(filepath):
    return send_from_directory(str(BASE_DIR / "uploads"), filepath)


# ---------------------------------------------------------------------------
# Settings page
# ---------------------------------------------------------------------------
@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        data = {
            "DEEPSEEK_API_KEY": request.form.get("DEEPSEEK_API_KEY", ""),
            "DEEPSEEK_MODEL": request.form.get("DEEPSEEK_MODEL", ""),
            "DEEPSEEK_ANALYZE_MODEL": request.form.get("DEEPSEEK_ANALYZE_MODEL", ""),
            "PORT": request.form.get("PORT", "5000"),
            "HOST": request.form.get("HOST", "0.0.0.0"),
            "GUI": request.form.get("GUI", "false"),
        }
        save_settings(data)
        # Mask key for display
        key = config.get("DEEPSEEK_API_KEY", "")
        masked = key[:4] + "****" + key[-4:] if len(key) > 8 else ("*" * len(key))
        return render_template("settings.html", config=config,
                               success="设置已保存。端口和 GUI 修改需重启应用生效。",
                               masked_key=masked)

    key = config.get("DEEPSEEK_API_KEY", "")
    masked = key[:4] + "****" + key[-4:] if len(key) > 8 else ("*" * len(key))
    return render_template("settings.html", config=config, masked_key=masked)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import threading

    # 修复 Windows 控制台中文乱码（方格问题）
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    log = get_logger()
    if log:
        log.system("info", "startup", port=config.get("PORT", 5000),
                   gui=config.get("GUI", "true"))

    port = int(config.get("PORT", 5000))
    host = config.get("HOST", "0.0.0.0")
    use_gui = config.get("GUI", "true").lower() in ("true", "1", "yes")
    use_headless = "--headless" in sys.argv

    # Detect LAN IP for mobile access
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = "127.0.0.1"

    model = config.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
    analyze_model = config.get("DEEPSEEK_ANALYZE_MODEL", config.get("DEEPSEEK_MODEL", "deepseek-v4-pro"))

    print(f"  Exdiary")
    print(f"  Local:    http://127.0.0.1:{port}")
    print(f"  Network:  http://{lan_ip}:{port}  (手机浏览器打开此地址)")
    print(f"  Extract:  {model}")
    print(f"  Analyze:  {analyze_model}")

    if use_headless or not use_gui:
        # Headless mode: just start Flask (for servers, or if GUI=false)
        print(f"  Mode:     headless (web only)")
        print(f"  Experiments: {BASE_DIR / 'experiments'}")
        app.run(host=host, port=port, debug=True)
    else:
        # Native desktop window mode
        try:
            import webview
        except ImportError:
            print("  pywebview not installed. Run: pip install pywebview")
            print("  Falling back to web mode...")
            app.run(host=host, port=port, debug=True)
            sys.exit()

        # Start Flask in a background thread
        def run_flask():
            app.run(host=host, port=port, debug=False, use_reloader=False)

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        # Launch native desktop window
        print(f"  Mode:     native desktop window")
        webview.create_window(
            title="Exdiary — 实验记录",
            url=f"http://127.0.0.1:{port}",
            width=1100,
            height=750,
            min_size=(800, 500),
            text_select=True,
        )
        webview.start()
        sys.exit(0)
