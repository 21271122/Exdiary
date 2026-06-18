"""
16 个 Agent 工具的 JSON Schema 定义（OpenAI function calling 格式）。
纯数据定义，不含执行逻辑。从 agent_v2.py 迁出。
"""

TOOL_LOAD_REFERENCE = {
    "type": "function",
    "function": {
        "name": "load_reference",
        "description": (
            "加载引用实验的完整数据（SOP、参数、结果、结论等）。"
            "仅接受 EXP ID 格式（如 EXP-2026-003）。"
            "用户说'跟003一样'时，请自行补全为 EXP-2026-003 后调用。"
            "模糊描述（如'上次的ZnO实验'）请用 search_experiments。"
            "结果写回messages，你可据此判断哪些字段可直接继承。"
            "已加载过的实验无需重复调用——数据已在 messages 中。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "实验编号（EXP-YYYY-NNN 格式）。不是模糊描述。",
                }
            },
            "required": ["refs"],
        },
    },
}

TOOL_SEARCH_EXPERIMENTS = {
    "type": "function",
    "function": {
        "name": "search_experiments",
        "description": (
            "在历史实验库中搜索。处理各种自然语言描述：\n"
            "- 时间指代：'上周的''最近的''上个月的'\n"
            "- 人员指代：'老张做的''我上次做的'\n"
            "- 状态指代：'失败的那个''成功的那个'\n"
            "- 材料指代：'做ZnO的那个''用了P25的'\n"
            "- 性能指代：'降解率最高的'\n"
            "返回候选列表。如用户确认候选，再调 load_reference 加载完整数据。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或自然语言描述",
                }
            },
            "required": ["query"],
        },
    },
}

TOOL_UPDATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_schema",
        "description": (
            "将本轮确认的信息写入Schema。写入后系统自动更新messages中的Schema状态摘要。"
            "注意: messages中已有当前Schema状态和引用实验数据，写入前请自行比对——"
            "新值与已有数据矛盾时，先向用户求证再写入，不要写入矛盾值后又覆盖。"
            "重要: generate_record 不会对字段值做二次 LLM 提取——它依赖你通过 update_schema 写入的数据质量。"
            "对数组字段(sop/tags/materials等)，如需整体替换或插入修正，先传[]清空再传完整列表: "
            "例如先 update_schema(sop:[]) 清空，再 update_schema(sop:[...完整步骤]) 写入正确顺序。"
            "嵌套对象(results/observations)必须传完整结构(含所有子字段)，不要只传部分子字段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "date": {"type": "string"},
                        "experimenter": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["planned", "running", "done", "failed", "repeated"],
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "purpose": {"type": "string"},
                        "materials": {"type": "array"},
                        "equipment": {"type": "array"},
                        "experimental_plan": {"type": "array"},
                        "sop": {"type": "array", "items": {"type": "string"}},
                        "process_parameters": {"type": "array"},
                        "observations": {"type": "object"},
                        "characterization": {"type": "array"},
                        "results": {"type": "object"},
                        "conclusion": {"type": "string"},
                        "next_steps": {"type": "array", "items": {"type": "string"}},
                    },
                    "description": "要更新的字段。增量更新——只传变化的。空列表[]或空对象{}表示清空。",
                },
                "round_summary": {
                    "type": "string",
                    "description": "一句话描述本轮收集/确认了哪些信息（用于日志）",
                },
            },
            "required": ["fields"],
        },
    },
}

TOOL_ASK_USER = {
    "type": "function",
    "function": {
        "name": "ask_user",
        "description": (
            "向用户提问。一次最多3个问题。问题应具体、可回答。"
            "依据: 看messages中Schema状态的缺失字段 + system prompt中的优先级清单，"
            "自己决定问什么、问几个。缺失的都是补充字段时可跳过直接结束。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 3,
                }
            },
            "required": ["questions"],
        },
    },
}

TOOL_GENERATE_RECORD = {
    "type": "function",
    "function": {
        "name": "generate_record",
        "description": (
            "生成实验记录草稿。当你判断实验信息已收集完毕、核心字段（目的、"
            "材料、步骤/参数、结果/结论）已填充时调用。调用后系统生成结构化"
            "记录并在前端展示预览面板，用户确认后保存。不要只输出纯文本等待——"
            "调用本工具是生成记录的唯一途径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_START_RECORD_THREAD = {
    "type": "function",
    "function": {
        "name": "start_record_thread",
        "description": (
            "开始一个实验记录线程。当用户明确表达要记录新实验时调用"
            "（如'记录新实验''帮我记一下''做了个...'等）。"
            "调用后标记对话进入记录模式，后续对话归属该线程。"
            "不要在查询、修改、闲聊时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_END_THREAD = {
    "type": "function",
    "function": {
        "name": "end_thread",
        "description": (
            "结束当前对话线程（record 或 analyze）。"
            "用户明确表示取消、结束、不继续时调用"
            "（如'算了''不记了''取消''结束线程'等）。"
            "调用后系统归档线程，自动清理状态，对话回到自由模式。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_START_ANALYZE_THREAD = {
    "type": "function",
    "function": {
        "name": "start_analyze_thread",
        "description": (
            "开始一个跨实验分析线程。当用户明确表达要分析实验数据时调用"
            "（如'分析一下''帮我看看这些实验''对比钙钛矿PCE'等）。"
            "调用后进入分析模式，后续对话归属该线程。"
            "不要在记录实验、查询、闲聊时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

TOOL_SELECT_EXPERIMENTS = {
    "type": "function",
    "function": {
        "name": "select_experiments",
        "description": (
            "向用户展示实验选择面板，让用户勾选参与分析的实验。"
            "当用户说'筛选''过滤''选实验''挑实验'等，或你需要让用户从多个实验中做选择时，"
            "必须调用本工具——不要用纯文本表格代替。"
            "传入 candidates 作为候选列表（通常来自 search_experiments 或 list_experiments）。"
            "可传入 preselected 预勾选已确定的实验。"
            "用户勾选确认后，选中的 EXP ID 列表作为 tool_result 回传。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array", "items": {"type": "object"},
                    "description": "候选实验列表，每项含 id/title/date/tags",
                },
                "preselected": {
                    "type": "array", "items": {"type": "string"},
                    "description": "预勾选的 EXP ID 列表",
                },
                "title": {
                    "type": "string",
                    "description": "面板标题，如'选择要分析的钙钛矿实验'",
                },
            },
            "required": ["candidates"],
        },
    },
}

TOOL_GENERATE_ANALYSIS = {
    "type": "function",
    "function": {
        "name": "generate_analysis",
        "description": (
            "执行跨实验分析并归档。当实验已选定、需求明确时调用。"
            "分析报告直接存储到本地分析历史，不在对话中显示全文。"
            "调用后自动结束分析线程，回到自由模式。"
            "这是生成分析报告的唯一途径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "分析问题，如'对比钙钛矿PCE趋势'",
                },
                "refs": {
                    "type": "array", "items": {"type": "string"},
                    "description": "参与分析的 EXP ID 列表，至少2个",
                },
            },
            "required": ["query", "refs"],
        },
    },
}

TOOL_MODIFY_ANALYSIS = {
    "type": "function",
    "function": {
        "name": "modify_analysis",
        "description": (
            "修改分析报告。支持三种操作模式：\n"
            "1. 修改文字：changes 传入新的完整 Markdown 文本，直接覆盖\n"
            "2. 新增实验并重新分析：additional_refs 传入额外 EXP ID，"
            "合并原有实验后重新运行分析\n"
            "3. 新增分析维度：additional_query 传入补充问题，"
            "在原报告基础上追加分析\n"
            "所有修改自动保存到 AnalysisStore。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "changes": {"type": "string", "description": "修改后的完整 Markdown 报告"},
                "additional_refs": {"type": "array", "items": {"type": "string"},
                                   "description": "额外纳入分析的 EXP ID"},
                "additional_query": {"type": "string", "description": "补充的分析问题/维度"},
            },
        },
    },
}

TOOL_READ_UPDATE_LOG = {
    "type": "function",
    "function": {
        "name": "read_update_log",
        "description": "读取某个实验的更新日志。当需要确认字段是否被修改过时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "exp_id": {"type": "string", "description": "实验编号 EXP-YYYY-NNN"},
                "since": {"type": "string", "description": "可选，只返回此时间之后的更新"},
                "limit": {"type": "integer", "description": "最多返回几条，默认 5"},
            },
            "required": ["exp_id"],
        },
    },
}

TOOL_MODIFY_EXPERIMENT = {
    "type": "function",
    "function": {
        "name": "modify_experiment",
        "description": (
            "修改实验字段。changes 中未出现的字段保持磁盘现有值不变（增量语义）。"
            "嵌套数组字段的值是完整的数组替换——请先通过 load_reference 获取当前完整数组，"
            "修改目标条目后传回完整数组。所有修改自动写入更新日志。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "要修改的实验编号列表"},
                "changes": {
                    "type": "object",
                    "description": "扁平字段名→新值映射。简单字段覆盖，数组字段完整替换。",
                },
                "description": {
                    "type": "string",
                    "description": "自然语言修改描述。与 changes 二选一。",
                },
            },
            "required": ["refs"],
        },
    },
}

TOOL_MANAGE_COLLECTION = {
    "type": "function",
    "function": {
        "name": "manage_collection",
        "description": "管理实验的收藏和置顶。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["pin", "unpin", "favorite", "unfavorite"]},
                "refs": {"type": "array", "items": {"type": "string"}},
                "collection": {"type": "string", "description": "收藏夹名称，默认'默认收藏夹'"},
            },
            "required": ["action", "refs"],
        },
    },
}

TOOL_QUERY_EXPERIMENT = {
    "type": "function",
    "function": {
        "name": "query_experiment",
        "description": "回答实验参数查询。用户提问模糊时可能需要多轮确认。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "用户的问题"},
                "refs": {"type": "array", "items": {"type": "string"},
                         "description": "要查询的实验编号列表"},
            },
            "required": ["question", "refs"],
        },
    },
}

TOOL_LIST_EXPERIMENTS = {
    "type": "function",
    "function": {
        "name": "list_experiments",
        "description": "按条件筛选实验列表。确定性执行，不调 LLM。",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "enum": ["planned", "running", "done", "failed", "repeated"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "experimenter": {"type": "string"},
                "since": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
            },
        },
    },
}

TOOLS_OPENAI_FORMAT = [
    TOOL_LOAD_REFERENCE,
    TOOL_SEARCH_EXPERIMENTS,
    TOOL_START_RECORD_THREAD,
    TOOL_UPDATE_SCHEMA,
    TOOL_ASK_USER,
    TOOL_GENERATE_RECORD,
    TOOL_READ_UPDATE_LOG,
    TOOL_MODIFY_EXPERIMENT,
    TOOL_MANAGE_COLLECTION,
    TOOL_QUERY_EXPERIMENT,
    TOOL_LIST_EXPERIMENTS,
    TOOL_END_THREAD,
    TOOL_START_ANALYZE_THREAD,
    TOOL_SELECT_EXPERIMENTS,
    TOOL_GENERATE_ANALYSIS,
    TOOL_MODIFY_ANALYSIS,
]
