"""模糊引用解析器——共享函数，被所有接受 refs 参数的工具复用。"""
import re


def resolve_refs(refs: list[str], store, llm=None) -> dict:
    """
    输入: 混合列表 ["EXP-2026-003", "上次的ZnO实验", "老张的钙钛矿"]
    输出: {"EXP-2026-003": {"status": "loaded", "data": {...}},
            "上次的ZnO实验": {"status": "ambiguous", "candidates": [...]}}

    规则:
    1. EXP ID 格式 (EXP-YYYY-NNN) → 直接加载，标记 loaded
    2. 非 EXP 格式 → 本地关键词搜索
       - 得分 >= 0.8 且唯一 → 自动解析，标记 loaded
       - 候选多或得分低 → 标记 ambiguous，返回 candidates
    3. 本地匹配差 → 调 LLM 语义搜索（如果 llm 已传入）
    """
    results = {}
    for ref in refs:
        ref = str(ref).strip()
        if not ref:
            continue

        # 规则 1: EXP ID 格式
        m = re.match(r"^(?:@)?(EXP-\d{4}-\d{3})$", ref, re.IGNORECASE)
        if m:
            exp_id = m.group(1).upper()
            exp = store.load(exp_id)
            if exp:
                results[ref] = {"status": "loaded", "data": exp}
            else:
                results[ref] = {"status": "error", "message": f"实验 {exp_id} 不存在"}
            continue

        # 规则 2: 本地关键词搜索
        all_exps = store.list_all_full()
        candidates = _fuzzy_search(ref, all_exps)

        if not candidates:
            results[ref] = {"status": "not_found", "message": "未找到匹配实验"}
            continue

        best = candidates[0]
        if best["score"] >= 0.8 and (len(candidates) == 1 or candidates[1]["score"] < 0.5):
            # 唯一高置信匹配 → 自动解析
            exp = store.load(best["id"])
            if exp:
                results[ref] = {"status": "loaded", "data": exp}
                continue

        # 规则 3: 返回候选列表
        results[ref] = {"status": "ambiguous", "candidates": candidates}

    return results


def _fuzzy_search(query: str, all_exps: list[dict]) -> list[dict]:
    """本地关键词搜索（含实验 ID）。"""
    if not query or len(query) < 2:
        return []

    results = []
    text_lower = query.lower()
    has_cjk = any('一' <= c <= '鿿' for c in query)

    for exp in all_exps:
        score = 0.0
        exp_id = (exp.get("id") or "").lower()
        title = (exp.get("title") or "").lower()
        tags = " ".join(exp.get("tags") or []).lower()
        purpose = (exp.get("purpose") or "")[:200].lower()
        mat_names = " ".join(
            m.get("name", "") for m in (exp.get("materials") or [])
            if isinstance(m, dict)
        ).lower()
        searchable = f"{exp_id} {title} {tags} {purpose} {mat_names}"

        if has_cjk:
            tokens = [text_lower]
            for i in range(len(text_lower) - 1):
                tokens.append(text_lower[i:i + 2])
        else:
            tokens = text_lower.split()

        for token in tokens:
            if len(token) >= 2 and token in searchable:
                score += 0.25

        for tag in (exp.get("tags") or []):
            if tag.lower() in text_lower:
                score += 0.3

        if score >= 0.2:
            results.append({
                "id": exp.get("id"),
                "title": exp.get("title", ""),
                "date": exp.get("date", ""),
                "tags": exp.get("tags", []),
                "score": min(score, 0.99),
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:5]
