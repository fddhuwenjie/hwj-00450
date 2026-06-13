#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL 语句分析与优化建议 CLI 工具
功能：SQL解析、索引建议、反模式检测、执行计划模拟、重写建议、批量分析
"""

import argparse
import sqlite3
import os
import re
import sys
import random
import json
from datetime import datetime
from collections import defaultdict


SQL_KEYWORDS = {
    'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'JOIN', 'LEFT', 'RIGHT', 'INNER',
    'OUTER', 'ON', 'GROUP', 'BY', 'ORDER', 'LIMIT', 'OFFSET', 'HAVING',
    'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'AS', 'DISTINCT',
    'UNION', 'ALL', 'EXISTS', 'IN', 'NOT', 'LIKE', 'BETWEEN', 'IS', 'NULL',
    'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'ASC', 'DESC', 'ASCENDING', 'DESCENDING',
}

AGGREGATE_FUNCTIONS = {'COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'GROUP_CONCAT'}

JOIN_KEYWORDS = {'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'NATURAL', 'JOIN'}
STOP_KEYWORDS = {'WHERE', 'GROUP', 'ORDER', 'LIMIT', 'HAVING', 'UNION', 'EXCEPT', 'INTERSECT'}


def split_sql_statements(content):
    statements = []
    buf = []
    in_string = False
    string_char = None
    i = 0
    while i < len(content):
        ch = content[i]
        if in_string:
            buf.append(ch)
            if ch == string_char:
                if i + 1 < len(content) and content[i + 1] == string_char:
                    buf.append(content[i + 1])
                    i += 1
                else:
                    in_string = False
        else:
            if ch in ("'", '"', '`'):
                in_string = True
                string_char = ch
                buf.append(ch)
            elif ch == ';':
                stmt = ''.join(buf).strip()
                if stmt:
                    statements.append(stmt)
                buf = []
            elif ch == '-' and i + 1 < len(content) and content[i + 1] == '-':
                while i < len(content) and content[i] != '\n':
                    i += 1
                continue
            elif ch == '/' and i + 1 < len(content) and content[i + 1] == '*':
                i += 2
                while i + 1 < len(content) and not (content[i] == '*' and content[i + 1] == '/'):
                    i += 1
                i += 1
                continue
            else:
                buf.append(ch)
        i += 1
    stmt = ''.join(buf).strip()
    if stmt:
        statements.append(stmt)
    return statements


def extract_sql_from_log(log_content):
    patterns = [
        r'(?i)((?:SELECT|INSERT|UPDATE|DELETE)\s+.+?;)',
        r'(?i)SQL\s*[:=]\s*["\'](.+?)["\']',
        r'(?i)执行SQL\s*[:：]\s*(.+?)(?:\n|$)',
    ]
    results = []
    for pat in patterns:
        for m in re.finditer(pat, log_content, re.DOTALL):
            sql = m.group(1) if m.lastindex and m.lastindex >= 1 else m.group(0)
            sql = sql.strip().rstrip(';').strip()
            if sql and len(sql) > 10:
                results.append(sql + ';')
    seen = set()
    uniq = []
    for s in results:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return uniq


def normalize_sql(sql):
    sql = re.sub(r'\s+', ' ', sql).strip()
    sql = sql.rstrip(';').strip()
    return sql


def tokenize_sql(sql):
    tokens = []
    buf = []
    i = 0
    in_string = False
    string_char = None
    while i < len(sql):
        ch = sql[i]
        if in_string:
            buf.append(ch)
            if ch == string_char:
                if i + 1 < len(sql) and sql[i + 1] == string_char:
                    buf.append(sql[i + 1])
                    i += 1
                else:
                    in_string = False
                    tokens.append(''.join(buf))
                    buf = []
        else:
            if ch in ("'", '"', '`'):
                if buf:
                    tokens.append(''.join(buf))
                    buf = []
                in_string = True
                string_char = ch
                buf = [ch]
            elif ch in '(),':
                if buf:
                    tokens.append(''.join(buf))
                    buf = []
                tokens.append(ch)
            elif ch.isspace():
                if buf:
                    tokens.append(''.join(buf))
                    buf = []
            elif ch in '=<>!+-*/%':
                if buf:
                    tokens.append(''.join(buf))
                    buf = []
                j = i
                while j < len(sql) and sql[j] in '=<>!+-*/%':
                    j += 1
                tokens.append(sql[i:j])
                i = j - 1
            else:
                buf.append(ch)
        i += 1
    if buf:
        tokens.append(''.join(buf))
    return [t for t in tokens if t.strip()]


def find_matching_paren(tokens, start):
    depth = 1
    i = start + 1
    while i < len(tokens) and depth > 0:
        if tokens[i] == '(':
            depth += 1
        elif tokens[i] == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(tokens) - 1


def print_tree(node, prefix="", is_last=True, is_root=True):
    lines = []
    label = str(node.get('label', node.get('name', 'NODE')))
    if is_root:
        lines.append("└── " + label)
    else:
        connector = "└── " if is_last else "├── "
        lines.append(prefix + connector + label)
    children = node.get('children', [])
    new_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(children):
        child_lines = print_tree(child, new_prefix, i == len(children) - 1, False)
        lines.extend(child_lines)
    return lines


class SQLParser:
    def __init__(self, sql):
        self.sql = normalize_sql(sql)
        self.tokens = tokenize_sql(sql)

    def parse(self):
        tokens = self.tokens
        if not tokens:
            return {'type': 'UNKNOWN', 'label': '空SQL', 'children': []}
        first = tokens[0].upper()
        if first == 'SELECT':
            return self._parse_select(tokens, 0)[0]
        elif first == 'INSERT':
            return self._parse_insert(tokens)
        elif first == 'UPDATE':
            return self._parse_update(tokens)
        elif first == 'DELETE':
            return self._parse_delete(tokens)
        return {'type': 'UNKNOWN', 'label': '未知语句: ' + tokens[0], 'children': []}

    def _parse_select(self, tokens, start):
        node = {'type': 'SELECT', 'label': 'SELECT 查询', 'children': []}
        i = start + 1
        distinct = False
        if i < len(tokens) and tokens[i].upper() == 'DISTINCT':
            distinct = True
            i += 1

        col_start = i
        while i < len(tokens) and tokens[i].upper() != 'FROM':
            i += 1
        select_tokens = tokens[col_start:i]
        cols = self._split_by_comma(select_tokens)
        col_nodes = []
        for c in cols:
            col_nodes.append(self._parse_expression(c))
        node['children'].append({'label': '选择列 (SELECT)', 'children': col_nodes})
        if distinct:
            node['children'].append({'label': '⚑ DISTINCT: 去重'})

        tables = []
        joins = []
        where_conditions = []
        group_by = []
        having = []
        order_by = []
        limit_val = None
        offset_val = None

        while i < len(tokens):
            tok = tokens[i].upper()
            if tok == 'FROM':
                i += 1
                from_start = i
                while i < len(tokens):
                    t = tokens[i].upper()
                    if t in STOP_KEYWORDS:
                        break
                    if t in JOIN_KEYWORDS:
                        break
                    i += 1
                from_tokens = tokens[from_start:i]
                tables = self._parse_from_clause(from_tokens)
                node['children'].append({
                    'label': '涉及表 (FROM)',
                    'children': [{'label': t} for t in tables],
                })
            elif tok in JOIN_KEYWORDS:
                join_info, i = self._parse_join(tokens, i)
                joins.append(join_info)
            elif tok == 'WHERE':
                i += 1
                where_start = i
                paren_depth = 0
                while i < len(tokens):
                    t = tokens[i].upper()
                    if t == '(':
                        paren_depth += 1
                    elif t == ')':
                        paren_depth -= 1
                    if paren_depth == 0 and t in ('GROUP', 'ORDER', 'LIMIT', 'HAVING'):
                        break
                    i += 1
                where_tokens = tokens[where_start:i]
                where_conditions = self._parse_where_conditions(where_tokens)
                node['children'].append({
                    'label': 'WHERE 条件',
                    'children': [{'label': c} for c in where_conditions],
                })
            elif tok == 'GROUP':
                i += 2
                gb_start = i
                paren_depth = 0
                while i < len(tokens):
                    t = tokens[i].upper()
                    if t == '(': paren_depth += 1
                    elif t == ')': paren_depth -= 1
                    if paren_depth == 0 and t in ('HAVING', 'ORDER', 'LIMIT'):
                        break
                    i += 1
                gb_tokens = tokens[gb_start:i]
                group_by = [self._expr_str(gb_tokens)]
                node['children'].append({
                    'label': '分组 (GROUP BY)',
                    'children': [{'label': g} for g in group_by],
                })
            elif tok == 'HAVING':
                i += 1
                hv_start = i
                paren_depth = 0
                while i < len(tokens):
                    t = tokens[i].upper()
                    if t == '(': paren_depth += 1
                    elif t == ')': paren_depth -= 1
                    if paren_depth == 0 and t in ('ORDER', 'LIMIT'):
                        break
                    i += 1
                hv_tokens = tokens[hv_start:i]
                having = [self._expr_str(hv_tokens)]
                node['children'].append({
                    'label': 'HAVING 条件',
                    'children': [{'label': h} for h in having],
                })
            elif tok == 'ORDER':
                i += 2
                ob_start = i
                paren_depth = 0
                while i < len(tokens):
                    t = tokens[i].upper()
                    if t == '(': paren_depth += 1
                    elif t == ')': paren_depth -= 1
                    if paren_depth == 0 and t == 'LIMIT':
                        break
                    i += 1
                ob_tokens = tokens[ob_start:i]
                order_by = [self._expr_str(ob_tokens)]
                node['children'].append({
                    'label': '排序 (ORDER BY)',
                    'children': [{'label': o} for o in order_by],
                })
            elif tok == 'LIMIT':
                i += 1
                if i < len(tokens):
                    limit_val = tokens[i]
                    i += 1
                if i < len(tokens) and tokens[i].upper() == 'OFFSET':
                    i += 1
                    if i < len(tokens):
                        offset_val = tokens[i]
                        i += 1
                elif i < len(tokens) and tokens[i] == ',':
                    i += 1
                    if i < len(tokens):
                        offset_val = limit_val
                        limit_val = tokens[i]
                        i += 1
                if limit_val:
                    node['children'].append({'label': f'LIMIT: {limit_val}'})
                if offset_val:
                    node['children'].append({'label': f'OFFSET: {offset_val}'})
            else:
                i += 1

        if joins:
            join_nodes = []
            for j in joins:
                c_nodes = []
                for c in j.get('conditions', []):
                    c_nodes.append({'label': 'ON: ' + c})
                join_nodes.append({
                    'label': f"{j['type']} -> {j['table']}",
                    'children': c_nodes,
                })
            node['children'].append({'label': '连接 (JOIN)', 'children': join_nodes})

        aggregations = self._find_aggregations(tokens)
        if aggregations:
            node['children'].append({
                'label': '聚合函数',
                'children': [{'label': a} for a in aggregations],
            })

        subqueries = self._find_subqueries(tokens)
        if subqueries:
            sq_nodes = []
            for idx, sq in enumerate(subqueries):
                short_sq = (sq[:80] + '...') if len(sq) > 80 else sq
                sq_nodes.append({
                    'label': f'子查询 #{idx + 1}',
                    'children': [{'label': 'SQL: ' + short_sq}],
                })
            node['children'].append({'label': '子查询', 'children': sq_nodes})

        node['_tables'] = tables
        node['_joins'] = joins
        node['_where_conditions'] = where_conditions
        node['_group_by'] = group_by
        node['_order_by'] = order_by
        node['_limit'] = limit_val
        node['_offset'] = offset_val
        node['_select_cols'] = self._extract_column_names(select_tokens)
        node['_aggregations'] = aggregations
        node['_subqueries'] = subqueries
        node['_having'] = having
        node['_distinct'] = distinct
        return node, i

    def _parse_insert(self, tokens):
        node = {'type': 'INSERT', 'label': 'INSERT 语句', 'children': []}
        i = 1
        table = ''
        cols = []
        values = []
        if i < len(tokens) and tokens[i].upper() == 'INTO':
            i += 1
        if i < len(tokens):
            table = tokens[i].strip('`"')
            i += 1
        node['children'].append({'label': '目标表: ' + table})
        if i < len(tokens) and tokens[i] == '(':
            end = find_matching_paren(tokens, i)
            col_tokens = tokens[i + 1:end]
            cols = [t.strip('`", ') for t in col_tokens if t not in ',()']
            i = end + 1
            if cols:
                node['children'].append({
                    'label': '插入列',
                    'children': [{'label': c} for c in cols],
                })
        if i < len(tokens) and tokens[i].upper() == 'VALUES':
            i += 1
            if i < len(tokens) and tokens[i] == '(':
                end = find_matching_paren(tokens, i)
                val_tokens = tokens[i + 1:end]
                values = [t for t in val_tokens if t != ',']
                i = end + 1
                if values:
                    val_nodes = [{'label': str(v)} for v in values[:10]]
                    if len(values) > 10:
                        val_nodes.append({'label': f'... 共 {len(values)} 个值'})
                    node['children'].append({'label': '插入值', 'children': val_nodes})
        if i < len(tokens) and tokens[i].upper() == 'SELECT':
            sub_node, _ = self._parse_select(tokens, i)
            node['children'].append({
                'label': '子查询 (INSERT ... SELECT)',
                'children': [sub_node],
            })
        node['_table'] = table
        node['_cols'] = cols
        return node

    def _parse_update(self, tokens):
        node = {'type': 'UPDATE', 'label': 'UPDATE 语句', 'children': []}
        i = 1
        table = ''
        set_clauses = []
        where_conditions = []
        if i < len(tokens):
            table = tokens[i].strip('`"')
            i += 1
        node['children'].append({'label': '目标表: ' + table})
        if i < len(tokens) and tokens[i].upper() == 'SET':
            i += 1
            set_start = i
            while i < len(tokens) and tokens[i].upper() != 'WHERE':
                i += 1
            set_tokens = tokens[set_start:i]
            set_clauses = self._split_by_comma(set_tokens)
            sc_nodes = [{'label': self._expr_str(s)} for s in set_clauses]
            node['children'].append({'label': 'SET 子句', 'children': sc_nodes})
        if i < len(tokens) and tokens[i].upper() == 'WHERE':
            i += 1
            where_start = i
            paren_depth = 0
            while i < len(tokens):
                t = tokens[i].upper()
                if t == '(': paren_depth += 1
                elif t == ')': paren_depth -= 1
                if paren_depth < 0:
                    break
                i += 1
            where_tokens = tokens[where_start:i]
            where_conditions = self._parse_where_conditions(where_tokens)
            node['children'].append({
                'label': 'WHERE 条件',
                'children': [{'label': c} for c in where_conditions],
            })
        node['_table'] = table
        node['_set_clauses'] = [self._expr_str(s) for s in set_clauses]
        node['_where_conditions'] = where_conditions
        return node

    def _parse_delete(self, tokens):
        node = {'type': 'DELETE', 'label': 'DELETE 语句', 'children': []}
        i = 1
        table = ''
        where_conditions = []
        if i < len(tokens) and tokens[i].upper() == 'FROM':
            i += 1
        if i < len(tokens):
            table = tokens[i].strip('`"')
            i += 1
        node['children'].append({'label': '目标表: ' + table})
        if i < len(tokens) and tokens[i].upper() == 'WHERE':
            i += 1
            where_start = i
            paren_depth = 0
            while i < len(tokens):
                t = tokens[i].upper()
                if t == '(': paren_depth += 1
                elif t == ')': paren_depth -= 1
                if paren_depth < 0:
                    break
                i += 1
            where_tokens = tokens[where_start:i]
            where_conditions = self._parse_where_conditions(where_tokens)
            node['children'].append({
                'label': 'WHERE 条件',
                'children': [{'label': c} for c in where_conditions],
            })
        node['_table'] = table
        node['_where_conditions'] = where_conditions
        return node

    def _parse_from_clause(self, tokens):
        tables = []
        parts = self._split_by_comma(tokens)
        for p in parts:
            if not p:
                continue
            t = p[0].strip('`"')
            if len(p) >= 3 and p[1].upper() == 'AS':
                t = t + ' AS ' + p[2].strip('`"')
            elif len(p) >= 2 and p[1].upper() != ',':
                t = t + ' ' + p[1].strip('`"')
            tables.append(t)
        return tables

    def _parse_join(self, tokens, start):
        join_type_parts = []
        i = start
        while i < len(tokens) and tokens[i].upper() != 'JOIN':
            join_type_parts.append(tokens[i].upper())
            i += 1
        join_type_parts.append('JOIN')
        join_type = ' '.join(join_type_parts)
        i += 1
        table = tokens[i].strip('`"')
        i += 1
        if i < len(tokens) and tokens[i].upper() == 'AS':
            if i + 1 < len(tokens):
                table = table + ' AS ' + tokens[i + 1].strip('`"')
            i += 2
        elif i < len(tokens) and tokens[i].upper() not in STOP_KEYWORDS and tokens[i].upper() not in JOIN_KEYWORDS and tokens[i] != 'ON':
            table = table + ' ' + tokens[i].strip('`"')
            i += 1
        conditions = []
        if i < len(tokens) and tokens[i].upper() == 'ON':
            i += 1
            cond_start = i
            paren_depth = 0
            while i < len(tokens):
                t = tokens[i].upper()
                if t == '(': paren_depth += 1
                elif t == ')': paren_depth -= 1
                if paren_depth == 0:
                    if t in STOP_KEYWORDS:
                        break
                    if t in JOIN_KEYWORDS and i + 1 < len(tokens) and tokens[i + 1].upper() == 'JOIN':
                        break
                    if t == 'JOIN':
                        break
                i += 1
            on_tokens = tokens[cond_start:i]
            conditions = self._parse_where_conditions(on_tokens)
        return {'type': join_type, 'table': table, 'conditions': conditions}, i

    def _parse_where_conditions(self, tokens):
        conditions = []
        current = []
        i = 0
        paren_depth = 0
        while i < len(tokens):
            tok = tokens[i]
            upper = tok.upper()
            if tok == '(':
                paren_depth += 1
                current.append(tok)
            elif tok == ')':
                paren_depth -= 1
                current.append(tok)
            elif upper in ('AND', 'OR') and paren_depth == 0:
                if current:
                    conditions.append(self._expr_str(current))
                    conditions.append('逻辑符: ' + upper)
                    current = []
                else:
                    conditions.append('逻辑符: ' + upper)
            else:
                current.append(tok)
            i += 1
        if current:
            conditions.append(self._expr_str(current))
        return conditions

    def _split_by_comma(self, tokens):
        parts = []
        current = []
        depth = 0
        for t in tokens:
            if t == '(':
                depth += 1
                current.append(t)
            elif t == ')':
                depth -= 1
                current.append(t)
            elif t == ',' and depth == 0:
                if current:
                    parts.append(current)
                current = []
            else:
                current.append(t)
        if current:
            parts.append(current)
        return parts

    def _parse_expression(self, tokens):
        expr_str = self._expr_str(tokens)
        label = expr_str
        is_agg = False
        for t in tokens:
            if t.upper() in AGGREGATE_FUNCTIONS:
                is_agg = True
                break
        if is_agg:
            label = '[聚合] ' + expr_str
        return {'label': label}

    def _expr_str(self, tokens):
        return ' '.join(tokens).strip()

    def _find_aggregations(self, tokens):
        aggs = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.upper() in AGGREGATE_FUNCTIONS and i + 1 < len(tokens) and tokens[i + 1] == '(':
                end = find_matching_paren(tokens, i + 1)
                aggs.append(self._expr_str(tokens[i:end + 1]))
                i = end
            i += 1
        return aggs

    def _find_subqueries(self, tokens):
        subqueries = []
        i = 0
        while i < len(tokens):
            if tokens[i] == '(':
                if i + 1 < len(tokens) and tokens[i + 1].upper() == 'SELECT':
                    end = find_matching_paren(tokens, i)
                    sub_sql = self._expr_str(tokens[i + 1:end])
                    subqueries.append(sub_sql)
                    i = end
            i += 1
        return subqueries

    def _extract_column_names(self, tokens):
        cols = []
        parts = self._split_by_comma(tokens)
        for p in parts:
            if len(p) == 1 and p[0] == '*':
                cols.append('*')
                continue
            col_parts = p
            for idx, t in enumerate(p):
                if t.upper() == 'AS':
                    col_parts = p[:idx]
                    break
            actual_col = self._expr_str(col_parts)
            if actual_col:
                cols.append(actual_col)
            else:
                cols.append(self._expr_str(p))
        return cols


class IndexAdvisor:
    def __init__(self, parsed, db_path=None):
        self.parsed = parsed
        self.db_path = db_path
        self.table_stats = {}
        if db_path and os.path.exists(db_path):
            self._load_table_stats()

    def _load_table_stats(self):
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            for t in tables:
                try:
                    cur.execute(f'SELECT COUNT(*) FROM "{t}"')
                    count = cur.fetchone()[0]
                    self.table_stats[t] = {'rows': count, 'columns': []}
                    cur.execute(f'PRAGMA table_info("{t}")')
                    cols_info = cur.fetchall()
                    for c in cols_info:
                        col_name = c[1]
                        col_type = c[2]
                        try:
                            cur.execute(f'SELECT COUNT(DISTINCT "{col_name}") FROM "{t}"')
                            distinct = cur.fetchone()[0]
                            selectivity = distinct / max(count, 1)
                        except:
                            distinct = 0
                            selectivity = 0
                        self.table_stats[t]['columns'].append({
                            'name': col_name,
                            'type': col_type,
                            'distinct': distinct,
                            'selectivity': selectivity,
                        })
                except:
                    pass
            conn.close()
        except:
            pass

    def advise(self):
        suggestions = []
        p = self.parsed
        tables = p.get('_tables', [])
        where_conditions = p.get('_where_conditions', [])
        joins = p.get('_joins', [])
        order_by = p.get('_order_by', [])
        current_table = tables[0].split()[0].split(' AS ')[0] if tables else ''

        equal_cols = defaultdict(list)
        range_cols = defaultdict(list)

        for cond in where_conditions:
            if cond.startswith('逻辑符:'):
                continue
            col, op, val = self._parse_condition(cond)
            if not col or not op:
                continue
            tbl = self._resolve_table(col, tables)
            if not tbl:
                tbl = current_table
            col_name = col.split('.')[-1].strip('`"')
            if op in ('=', 'IN', 'IS'):
                equal_cols[tbl].append(col_name)
            elif op in ('>', '<', '>=', '<=', 'BETWEEN', 'LIKE'):
                range_cols[tbl].append(col_name)

        for tbl, cols in equal_cols.items():
            unique_cols = list(dict.fromkeys(cols))
            if len(unique_cols) == 1:
                suggestions.append({
                    'severity': 'medium',
                    'type': '单列索引',
                    'table': tbl,
                    'columns': unique_cols,
                    'sql': f'CREATE INDEX idx_{tbl}_{unique_cols[0]} ON "{tbl}"("{unique_cols[0]}");',
                    'reason': f'WHERE 中等值条件列: {unique_cols[0]}',
                })
            elif len(unique_cols) > 1:
                ordered_cols = self._sort_by_selectivity(tbl, unique_cols)
                suggestions.append({
                    'severity': 'high',
                    'type': '复合索引',
                    'table': tbl,
                    'columns': ordered_cols,
                    'sql': f'CREATE INDEX idx_{tbl}_{"_".join(ordered_cols)} ON "{tbl}"({", ".join(f"\"{c}\"" for c in ordered_cols)});',
                    'reason': f'WHERE 多列 AND 条件（按选择性排序）: {", ".join(ordered_cols)}',
                })

        for tbl, cols in range_cols.items():
            for col in cols:
                suggestions.append({
                    'severity': 'medium',
                    'type': '范围查询索引',
                    'table': tbl,
                    'columns': [col],
                    'sql': f'CREATE INDEX idx_{tbl}_{col} ON "{tbl}"("{col}");',
                    'reason': f'范围条件列: {col}',
                })

        for j in joins:
            join_tbl = j['table'].split()[0].split(' AS ')[0]
            for cond in j.get('conditions', []):
                col, op, val = self._parse_condition(cond)
                if col and op == '=':
                    left_col = col.split('.')[-1].strip('`"')
                    right_col = val.split('.')[-1].strip('`"') if val else ''
                    suggestions.append({
                        'severity': 'high',
                        'type': 'JOIN 索引',
                        'table': join_tbl,
                        'columns': [right_col] if right_col else [left_col],
                        'sql': f'CREATE INDEX idx_{join_tbl}_{right_col or left_col} ON "{join_tbl}"("{right_col or left_col}");',
                        'reason': f'JOIN 关联列 ({j["type"]}): {right_col or left_col}',
                    })

        if order_by and tables:
            for tbl_raw in tables:
                tbl = tbl_raw.split()[0].split(' AS ')[0]
                ob_cols = []
                for ob in order_by:
                    for c in self._parse_order_cols(ob):
                        ob_col = c.split('.')[-1].strip('`"')
                        ob_cols.append(ob_col)
                if ob_cols:
                    eq_cols = equal_cols.get(tbl, [])
                    if eq_cols:
                        cover_cols = list(dict.fromkeys(eq_cols + ob_cols))
                        suggestions.append({
                            'severity': 'medium',
                            'type': '覆盖索引',
                            'table': tbl,
                            'columns': cover_cols,
                            'sql': f'CREATE INDEX idx_{tbl}_cover ON "{tbl}"({", ".join(f"\"{c}\"" for c in cover_cols)});',
                            'reason': f'WHERE + ORDER BY 组合覆盖索引: {", ".join(cover_cols)}',
                        })
                    else:
                        suggestions.append({
                            'severity': 'low',
                            'type': '排序索引',
                            'table': tbl,
                            'columns': ob_cols,
                            'sql': f'CREATE INDEX idx_{tbl}_order ON "{tbl}"({", ".join(f"\"{c}\"" for c in ob_cols)});',
                            'reason': f'ORDER BY 列: {", ".join(ob_cols)}',
                        })

        seen = set()
        unique_sug = []
        for s in suggestions:
            key = (s['table'], tuple(s['columns']), s['type'])
            if key not in seen:
                seen.add(key)
                unique_sug.append(s)
        return unique_sug

    def _parse_condition(self, cond):
        ops_order = ['>=', '<=', '!=', '<>', '=', '>', '<']
        for op in ops_order:
            if op in cond:
                idx = cond.find(op)
                left = cond[:idx].strip()
                right = cond[idx + len(op):].strip()
                return left, op, right
        words = cond.split()
        for idx, w in enumerate(words):
            uw = w.upper()
            if uw in ('LIKE', 'BETWEEN', 'IN', 'IS'):
                left = ' '.join(words[:idx]).strip()
                rest = ' '.join(words[idx + 1:]).strip()
                if uw == 'IS' and idx + 1 < len(words) and words[idx + 1].upper() == 'NOT':
                    return left, 'IS NOT', ' '.join(words[idx + 2:]).strip()
                return left, uw, rest
        return None, None, None

    def _resolve_table(self, col, tables):
        if '.' in col:
            prefix = col.split('.')[0].strip('`"')
            for t in tables:
                parts = t.split()
                base = parts[0]
                alias = parts[-1] if len(parts) > 1 else base
                if base == prefix or alias == prefix:
                    return base.split(' AS ')[0]
        return None

    def _sort_by_selectivity(self, table, cols):
        if table in self.table_stats:
            col_map = {}
            for c in self.table_stats[table].get('columns', []):
                col_map[c['name']] = c.get('selectivity', 0)
            return sorted(cols, key=lambda c: col_map.get(c, 0.5), reverse=True)
        return cols

    def _parse_order_cols(self, order_str):
        cols = []
        for part in order_str.split(','):
            c = part.strip()
            for kw in [' ASC', ' DESC', ' ASCENDING', ' DESCENDING']:
                if c.upper().endswith(kw):
                    c = c[:-len(kw)].strip()
                    break
            cols.append(c)
        return cols


class AntiPatternDetector:
    def __init__(self, sql, parsed, db_path=None):
        self.sql = sql
        self.sql_upper = sql.upper()
        self.parsed = parsed
        self.db_path = db_path
        self.issues = []

    def detect(self):
        self._check_select_star()
        self._check_function_on_column()
        self._check_implicit_conversion()
        self._check_not_in_subquery()
        self._check_prefix_like()
        self._check_no_limit()
        self._check_cartesian_product()
        self._check_or_conditions()
        self._check_distinct_overuse()
        return self.issues

    def _check_select_star(self):
        select_cols = self.parsed.get('_select_cols', [])
        if '*' in select_cols:
            self.issues.append({
                'severity': 'high',
                'pattern': 'SELECT *',
                'description': '使用 SELECT * 返回所有列',
                'impact': '传输不必要的数据，无法利用覆盖索引，Schema 变更易出问题',
                'suggestion': '明确列出所需列名',
            })

    def _check_function_on_column(self):
        tokens = tokenize_sql(self.sql)
        i = 0
        where_idx = -1
        while i < len(tokens):
            if tokens[i].upper() == 'WHERE':
                where_idx = i
                break
            i += 1
        if where_idx < 0:
            return
        i = where_idx + 1
        depth = 0
        found = None
        while i < len(tokens):
            t = tokens[i]
            if t == '(':
                depth += 1
                if i >= 1:
                    prev = tokens[i - 1]
                    prev_upper = prev.upper()
                    if (len(prev) > 1 and not prev.isdigit()
                            and prev_upper not in SQL_KEYWORDS
                            and prev_upper not in AGGREGATE_FUNCTIONS
                            and prev_upper not in ('AND', 'OR', 'NOT')
                            and prev not in ('=', '>', '<', '>=', '<=', '!=', '<>')
                            and prev not in ('(', ')', ',')):
                        end = find_matching_paren(tokens, i)
                        if end + 1 < len(tokens):
                            next_tok = tokens[end + 1]
                            if next_tok in ('=', '>', '<', '>=', '<=', '!=', '<>', 'LIKE', 'IN'):
                                found = prev
                                break
                i += 1
            elif t == ')':
                depth -= 1
                i += 1
            elif depth == 0 and t.upper() in ('GROUP', 'ORDER', 'LIMIT', 'HAVING'):
                break
            else:
                i += 1
        if found:
            self.issues.append({
                'severity': 'high',
                'pattern': 'WHERE 中对列使用函数',
                'description': f'在 WHERE 子句中对列使用函数: {found}(...)',
                'impact': '导致索引失效，触发全表扫描',
                'suggestion': '将函数移到等式另一侧，或建立函数索引',
            })

    def _check_implicit_conversion(self):
        patterns = [
            r"['\"]\d+['\"]\s*[=<>]",
            r"[=<>]\s*['\"]\d+['\"]",
        ]
        for pat in patterns:
            if re.search(pat, self.sql):
                self.issues.append({
                    'severity': 'medium',
                    'pattern': '隐式类型转换',
                    'description': '检测到数字与字符串的比较，可能存在隐式类型转换',
                    'impact': '索引失效，查询性能下降',
                    'suggestion': '确保比较两侧类型一致，使用显式类型转换',
                })
                break

    def _check_not_in_subquery(self):
        if re.search(r'NOT\s+IN\s*\(\s*SELECT', self.sql, re.IGNORECASE):
            self.issues.append({
                'severity': 'high',
                'pattern': 'NOT IN 子查询',
                'description': '使用 NOT IN (SELECT ...) 子查询',
                'impact': '子查询结果含 NULL 时返回空集，性能较差',
                'suggestion': '改用 NOT EXISTS 或 LEFT JOIN ... IS NULL',
            })

    def _check_prefix_like(self):
        tokens = tokenize_sql(self.sql)
        for i, t in enumerate(tokens):
            if t.upper() == 'LIKE' and i + 1 < len(tokens):
                pattern = tokens[i + 1]
                stripped = pattern.strip("'\"")
                if stripped.startswith('%') or stripped.startswith('_'):
                    self.issues.append({
                        'severity': 'high',
                        'pattern': '前缀通配符 LIKE',
                        'description': 'LIKE 模式以通配符开头: ' + pattern,
                        'impact': '无法使用 B-Tree 索引，全表扫描',
                        'suggestion': '考虑全文检索，或调整为后缀匹配 + 应用层处理',
                    })
                    break

    def _check_no_limit(self):
        p = self.parsed
        if p.get('type') == 'SELECT' and not p.get('_limit'):
            tables = p.get('_tables', [])
            if tables and self.db_path and os.path.exists(self.db_path):
                try:
                    conn = sqlite3.connect(self.db_path)
                    cur = conn.cursor()
                    large = False
                    for t in tables:
                        base = t.split()[0].split(' AS ')[0]
                        try:
                            cur.execute(f'SELECT COUNT(*) FROM "{base}"')
                            cnt = cur.fetchone()[0]
                            if cnt > 1000:
                                large = True
                                break
                        except:
                            pass
                    conn.close()
                    if large:
                        self.issues.append({
                            'severity': 'medium',
                            'pattern': '大表查询无 LIMIT',
                            'description': '对大表查询未使用 LIMIT 限制结果集',
                            'impact': '返回大量数据，内存与网络开销大',
                            'suggestion': '添加 LIMIT 分页，或确认确实需要全量数据',
                        })
                except:
                    pass

    def _check_cartesian_product(self):
        p = self.parsed
        tables = p.get('_tables', [])
        joins = p.get('_joins', [])
        where = p.get('_where_conditions', [])
        has_cross = any('CROSS' in j['type'].upper() for j in joins)
        eq_conditions = [c for c in where if not c.startswith('逻辑符:') and '=' in c]
        if len(tables) >= 2 and not joins and not eq_conditions:
            self.issues.append({
                'severity': 'critical',
                'pattern': '笛卡尔积',
                'description': '多表查询缺少 JOIN 条件',
                'impact': '产生 M×N 行，严重性能问题',
                'suggestion': '添加正确的 JOIN ... ON 条件',
            })
        if has_cross:
            self.issues.append({
                'severity': 'critical',
                'pattern': '笛卡尔积 (CROSS JOIN)',
                'description': '显式使用 CROSS JOIN',
                'impact': '产生笛卡尔积',
                'suggestion': '确认是否为业务必需，否则改为 INNER/LEFT JOIN',
            })

    def _check_or_conditions(self):
        where = self.parsed.get('_where_conditions', [])
        or_count = sum(1 for c in where if c == '逻辑符: OR')
        if or_count >= 2:
            self.issues.append({
                'severity': 'low',
                'pattern': '多个 OR 条件',
                'description': f'WHERE 中有 {or_count} 个 OR 逻辑符',
                'impact': '可能导致索引选择困难',
                'suggestion': '考虑改写为 UNION ALL',
            })

    def _check_distinct_overuse(self):
        if self.parsed.get('_distinct'):
            self.issues.append({
                'severity': 'low',
                'pattern': 'DISTINCT 去重',
                'description': '使用了 DISTINCT 去重',
                'impact': '需要排序去重开销',
                'suggestion': '检查是否可通过 EXISTS / JOIN 优化避免 DISTINCT',
            })


class ExplainPlanAnalyzer:
    def __init__(self, sql, db_path):
        self.sql = sql
        self.db_path = db_path

    def analyze(self):
        if not self.db_path or not os.path.exists(self.db_path):
            return [{'level': 'warn', 'detail': '未指定或不存在数据库文件，无法执行 EXPLAIN QUERY PLAN'}]
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute('EXPLAIN QUERY PLAN ' + self.sql)
            rows = cur.fetchall()
            conn.close()
            return self._translate(rows)
        except Exception as e:
            return [{'level': 'error', 'detail': '执行 EXPLAIN 失败: ' + str(e)}]

    def _translate(self, rows):
        results = []
        for row in rows:
            detail = str(row[-1]) if isinstance(row, tuple) else str(row)
            translated = self._translate_detail(detail)
            du = detail.upper()
            level = 'info'
            if re.search(r'SCAN(?:\s+TABLE)?\s+\S+', du) and 'SUBQUERY' not in du:
                level = 'warn'
            if 'TEMP B-TREE' in du:
                level = 'warn'
            if 'USING COVERING INDEX' in du or 'USING INTEGER PRIMARY KEY' in du:
                level = 'good'
            if re.search(r'SEARCH(?:\s+TABLE)?\s+\S+\s+USING', du):
                level = 'good'
            results.append({
                'level': level,
                'raw': detail,
                'detail': translated,
            })
        return results

    def _translate_detail(self, detail):
        du = detail.upper()
        scan_match = re.search(r'SCAN(?:\s+TABLE)?\s+(\S+)', detail, re.IGNORECASE)
        if 'SCAN' in du and 'SUBQUERY' not in du:
            if scan_match:
                tbl = scan_match.group(1)
                return f'全表扫描表 {tbl}（逐行读取，性能较差）'
        search_match = re.search(r'SEARCH(?:\s+TABLE)?\s+(\S+)', detail, re.IGNORECASE)
        if search_match and 'USING INTEGER PRIMARY KEY' in du:
            tbl = search_match.group(1)
            return f'使用整数主键索引查找 {tbl}（最优路径）'
        if search_match and 'USING COVERING INDEX' in du:
            m = re.search(r'USING COVERING INDEX (\S+)', detail, re.IGNORECASE)
            idx = m.group(1) if m else ''
            tbl = search_match.group(1)
            suffix = f' -> {idx}（无需回表，高效）' if idx else '（无需回表，高效）'
            return f'使用覆盖索引查找 {tbl}{suffix}'
        if search_match and 'USING INDEX' in du:
            m = re.search(r'USING (?:INDEX\s+)?(\S+)', detail, re.IGNORECASE)
            idx = ''
            if m:
                idx = m.group(1)
                if idx.upper() == 'INDEX':
                    idx = ''
            tbl = search_match.group(1)
            suffix = f' 使用索引 {idx}（需回表）' if idx else '（需回表）'
            return f'索引查找 {tbl}{suffix}'
        if 'TEMP B-TREE FOR ORDER BY' in du:
            return '使用临时 B-Tree 进行 ORDER BY 排序（内存/磁盘开销）'
        if 'TEMP B-TREE FOR GROUP BY' in du:
            return '使用临时 B-Tree 进行 GROUP BY 分组'
        if 'TEMP B-TREE FOR DISTINCT' in du:
            return '使用临时 B-Tree 进行 DISTINCT 去重'
        if 'TEMP B-TREE' in du:
            return '使用临时 B-Tree（性能开销）'
        if 'LEFT JOIN' in du:
            return '执行左连接 (LEFT JOIN)'
        if 'RIGHT JOIN' in du:
            return '执行右连接 (RIGHT JOIN)'
        if 'INNER JOIN' in du:
            return '执行内连接 (INNER JOIN)'
        if 'CROSS JOIN' in du:
            return '执行交叉连接（笛卡尔积，⚠️性能风险）'
        if 'CORRELATED SCALAR SUBQUERY' in du:
            return '执行关联标量子查询（每行执行一次，性能差）'
        if 'LIST SUBQUERY' in du:
            return '执行列表子查询 (IN ...)'
        if 'SUBQUERY' in du:
            return '执行子查询'
        if 'UNION ALL' in du:
            return 'UNION ALL 合并结果（不去重，高效）'
        if 'UNION' in du:
            return 'UNION 合并结果（去重，需排序）'
        if 'USE TEMP B-TREE' in du:
            return '使用临时 B-Tree 处理'
        if 'LIMIT' in du:
            m = re.search(r'LIMIT (\d+)', detail, re.IGNORECASE)
            if m:
                return f'限制返回行数: {m.group(1)}'
            return '限制返回行数'
        return detail


class SQLRewriter:
    def __init__(self, sql, parsed, db_path=None):
        self.sql = sql
        self.parsed = parsed
        self.db_path = db_path
        self.suggestions = []

    def rewrite(self):
        self._rewrite_select_star()
        self._rewrite_not_in_to_exists()
        self._rewrite_subquery_to_join()
        self._rewrite_or_to_union()
        return self.suggestions

    def _estimate(self, original_cost, new_cost):
        if original_cost <= 0:
            return '无法估算 (缺少数据库统计信息)'
        ratio = (original_cost - new_cost) / max(original_cost, 1) * 100
        return f'预计性能提升约 {ratio:.1f}% (估算成本: {original_cost} → {new_cost}，基于行数×操作模型)'

    def _rows(self, table):
        if not self.db_path or not os.path.exists(self.db_path):
            return 1000
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            cnt = cur.fetchone()[0]
            conn.close()
            return max(cnt, 1)
        except:
            return 1000

    def _rewrite_select_star(self):
        select_cols = self.parsed.get('_select_cols', [])
        tables = self.parsed.get('_tables', [])
        if '*' in select_cols and tables:
            table = tables[0].split()[0].split(' AS ')[0]
            if self.db_path and os.path.exists(self.db_path):
                try:
                    conn = sqlite3.connect(self.db_path)
                    cur = conn.cursor()
                    cur.execute(f'PRAGMA table_info("{table}")')
                    cols = [f'"{r[1]}"' for r in cur.fetchall()]
                    conn.close()
                    if cols:
                        new_sql = re.sub(
                            r'SELECT(\s+DISTINCT)?\s+\*',
                            lambda m: 'SELECT' + (m.group(1) or '') + ' ' + ', '.join(cols),
                            self.sql, count=1, flags=re.IGNORECASE)
                        rc = self._rows(table)
                        self.suggestions.append({
                            'severity': 'high',
                            'title': '替换 SELECT * 为明确列',
                            'before': self.sql[:300],
                            'after': new_sql[:300] if len(new_sql) > 300 else new_sql,
                            'improvement': self._estimate(rc * len(cols), rc * max(3, len(cols) // 3)),
                        })
                except:
                    pass

    def _rewrite_not_in_to_exists(self):
        m = re.search(r'(\S+)\s+NOT\s+IN\s*\(\s*(SELECT.+?)\)',
                      self.sql, re.IGNORECASE | re.DOTALL)
        if not m:
            return
        outer_col = m.group(1)
        subquery = m.group(2).strip()
        sub_col_match = re.search(r'SELECT\s+(.+?)\s+FROM', subquery, re.IGNORECASE | re.DOTALL)
        sub_col = sub_col_match.group(1).strip() if sub_col_match else 'id'
        sub_from_match = re.search(r'FROM\s+(\S+)', subquery, re.IGNORECASE)
        sub_table = sub_from_match.group(1) if sub_from_match else 'sub_tbl'
        sub_where_match = re.search(r'WHERE\s+(.+)', subquery, re.IGNORECASE | re.DOTALL)
        sub_where = sub_where_match.group(1).strip() if sub_where_match else ''
        not_in_expr = m.group(0)
        inner_col = sub_col.split('.')[-1].strip('`"')
        outer_simple = outer_col.split('.')[-1].strip('`"')
        exists_cond = f"NOT EXISTS (SELECT 1 FROM {sub_table} st WHERE st.{inner_col} = {outer_simple}"
        if sub_where and sub_where not in ('', ';'):
            exists_cond += ' AND (' + sub_where.rstrip(';').rstrip(')') + ')'
        exists_cond += ')'
        new_sql = self.sql.replace(not_in_expr, exists_cond)
        rc_outer = self._rows(self.parsed.get('_tables', ['t'])[0].split()[0].split(' AS ')[0])
        rc_inner = self._rows(sub_table)
        self.suggestions.append({
            'severity': 'high',
            'title': 'NOT IN 改写为 NOT EXISTS',
            'before': self.sql[:300],
            'after': new_sql[:400] if len(new_sql) > 400 else new_sql,
            'improvement': self._estimate(rc_outer * rc_inner, rc_outer * 3),
        })

    def _rewrite_subquery_to_join(self):
        subqueries = self.parsed.get('_subqueries', [])
        tables = self.parsed.get('_tables', [])
        if not subqueries or not tables:
            return
        sq = subqueries[0]
        m_in = re.search(r'(?<!NOT\s)(\w+(?:\.\w+)?)\s+IN\s*\(\s*SELECT', self.sql, re.IGNORECASE)
        if not m_in:
            return
        outer_col = m_in.group(1)
        if outer_col.upper() == 'NOT':
            return
        sub_col_match = re.search(r'SELECT\s+(.+?)\s+FROM', sq, re.IGNORECASE | re.DOTALL)
        sub_col = sub_col_match.group(1).strip() if sub_col_match else 'id'
        sub_from_match = re.search(r'FROM\s+(\S+)', sq, re.IGNORECASE)
        sub_table = sub_from_match.group(1) if sub_from_match else 'sub_t'
        outer_table = tables[0].split()[0].split(' AS ')[0]
        outer_simple = outer_col.split('.')[-1].strip('`"')
        inner_simple = sub_col.split('.')[-1].strip('`"')
        distinct_kw = 'DISTINCT ' if not self.parsed.get('_distinct') else ''
        base_match = re.match(r'(SELECT\s+(?:DISTINCT\s+)?)(.+?)(\s+FROM\s+' + re.escape(outer_table) + r')',
                              self.sql, re.IGNORECASE | re.DOTALL)
        if base_match:
            new_sql = (f"{base_match.group(1)}{distinct_kw}{base_match.group(2).strip()}"
                       f"{base_match.group(3)} JOIN {sub_table} sq ON sq.{inner_simple} = {outer_simple}")
            rest = self.sql[base_match.end():]
            in_match = re.search(r'\s*(?:AND\s+|WHERE\s+)?' + re.escape(outer_col) + r'\s+IN\s*\(.+?\)',
                                 rest, re.IGNORECASE | re.DOTALL)
            if in_match:
                rest = rest[:in_match.start()] + rest[in_match.end():]
            new_sql += rest.rstrip(';').rstrip()
            rc_outer = self._rows(outer_table)
            self.suggestions.append({
                'severity': 'medium',
                'title': 'IN 子查询改写为 JOIN',
                'before': self.sql[:300],
                'after': new_sql[:400] if len(new_sql) > 400 else new_sql,
                'improvement': self._estimate(rc_outer * 10, rc_outer * 2),
            })

    def _rewrite_or_to_union(self):
        where = self.parsed.get('_where_conditions', [])
        or_count = sum(1 for c in where if c == '逻辑符: OR')
        if or_count < 1 or self.parsed.get('type') != 'SELECT':
            return
        groups = []
        current = []
        for cond in where:
            if cond == '逻辑符: OR':
                if current:
                    groups.append(current)
                current = []
            elif cond == '逻辑符: AND':
                continue
            else:
                current.append(cond)
        if current:
            groups.append(current)
        if len(groups) < 2:
            return
        tables = self.parsed.get('_tables', ['t'])
        tbl_clause = ' FROM ' + tables[0]
        base_match = re.match(r'(SELECT.+?)' + re.escape(tbl_clause),
                              self.sql, re.IGNORECASE | re.DOTALL)
        if not base_match:
            return
        select_part = base_match.group(1)
        rest = self.sql[base_match.end():]
        where_match = re.match(r'(\s*WHERE\s+).+', rest, re.IGNORECASE | re.DOTALL)
        tail = ''
        if where_match:
            rest_no_where = rest[where_match.end():]
            tail_rest_match = re.search(r'\s+(GROUP|ORDER|LIMIT|HAVING)\s+',
                                        rest_no_where, re.IGNORECASE)
            if tail_rest_match:
                tail = rest_no_where[tail_rest_match.start():]
        union_parts = []
        for g in groups:
            g_cond = ' AND '.join(g)
            union_parts.append(f"{select_part}{tbl_clause} WHERE {g_cond}")
        new_sql = '\nUNION ALL\n'.join(union_parts) + (tail if tail else '')
        rc = self._rows(tables[0].split()[0].split(' AS ')[0])
        self.suggestions.append({
            'severity': 'medium',
            'title': 'OR 条件改写为 UNION ALL',
            'before': self.sql[:300],
            'after': new_sql[:400] if len(new_sql) > 400 else new_sql,
            'improvement': self._estimate(rc * (len(groups) + 1), rc * 2),
        })


class BatchAnalyzer:
    def __init__(self, db_path=None):
        self.db_path = db_path

    def analyze_file(self, filepath, is_log=False):
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        if is_log:
            sqls = extract_sql_from_log(content)
        else:
            sqls = split_sql_statements(content)
        results = []
        for idx, sql in enumerate(sqls, 1):
            results.append(self._analyze_single(sql, idx))
        return results

    def _analyze_single(self, sql, idx=1):
        parser = SQLParser(sql)
        parsed = parser.parse()
        detector = AntiPatternDetector(sql, parsed, self.db_path)
        issues = detector.detect()
        advisor = IndexAdvisor(parsed, self.db_path)
        indexes = advisor.advise()
        rewriter = SQLRewriter(sql, parsed, self.db_path)
        rewrites = rewriter.rewrite()
        explain = ExplainPlanAnalyzer(sql, self.db_path)
        plan = explain.analyze()
        return {
            'id': idx,
            'sql': sql,
            'issues': issues,
            'indexes': indexes,
            'rewrites': rewrites,
            'plan': plan,
            'parsed': parsed,
        }

    def generate_markdown_report(self, results, output_path=None):
        total = len(results)
        all_issues = []
        all_indexes = []
        all_rewrites = []
        for r in results:
            all_issues.extend(r['issues'])
            all_indexes.extend(r['indexes'])
            all_rewrites.extend(r['rewrites'])
        sev_count = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
        for i in all_issues:
            sev_count[i['severity']] = sev_count.get(i['severity'], 0) + 1
        lines = []
        lines.append('# SQL 优化分析报告')
        lines.append('')
        lines.append('**生成时间**: ' + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        lines.append('')
        lines.append('## 概览统计')
        lines.append('')
        lines.append('| 指标 | 数量 |')
        lines.append('|------|------|')
        lines.append(f'| 分析 SQL 总数 | {total} |')
        lines.append(f'| 发现问题总数 | {len(all_issues)} |')
        lines.append(f'| - 严重 (Critical) | {sev_count["critical"]} |')
        lines.append(f'| - 高 (High) | {sev_count["high"]} |')
        lines.append(f'| - 中 (Medium) | {sev_count["medium"]} |')
        lines.append(f'| - 低 (Low) | {sev_count["low"]} |')
        lines.append(f'| 索引建议数 | {len(all_indexes)} |')
        lines.append(f'| 重写建议数 | {len(all_rewrites)} |')
        lines.append('')
        lines.append('## 问题按严重程度排序')
        lines.append('')
        sev_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        sorted_issues = sorted(all_issues, key=lambda x: sev_order.get(x['severity'], 99))
        sev_icon = {'critical': '🔴 严重', 'high': '🟠 高', 'medium': '🟡 中', 'low': '🟢 低'}
        for idx, issue in enumerate(sorted_issues, 1):
            lbl = sev_icon.get(issue['severity'], '⚪ 未知')
            lines.append(f'### {idx}. {lbl} - {issue["pattern"]}')
            lines.append('')
            lines.append(f'- **描述**: {issue["description"]}')
            lines.append(f'- **影响**: {issue["impact"]}')
            lines.append(f'- **建议**: {issue["suggestion"]}')
            lines.append('')
        lines.append('## 索引建议')
        lines.append('')
        for idx, sug in enumerate(all_indexes, 1):
            lines.append(f'### {idx}. {sug["type"]} - {sug["table"]}')
            lines.append('')
            lines.append(f'- **列**: {", ".join(sug["columns"])}')
            lines.append(f'- **原因**: {sug["reason"]}')
            lines.append(f"- **SQL**: `{sug['sql']}`")
            lines.append('')
        lines.append('## 详细 SQL 分析')
        lines.append('')
        for r in results:
            lines.append(f"### SQL #{r['id']}")
            lines.append('')
            lines.append('```sql')
            lines.append(r['sql'][:500])
            lines.append('```')
            lines.append('')
            if r['issues']:
                lines.append('**问题**:')
                for issue in r['issues']:
                    lines.append(f"- [{issue['severity'].upper()}] {issue['pattern']}: {issue['description']}")
                lines.append('')
            if r['indexes']:
                lines.append('**索引建议**:')
                for sug in r['indexes']:
                    lines.append(f"- {sug['type']}: `{sug['sql']}`")
                lines.append('')
            if r['rewrites']:
                lines.append('**重写建议**:')
                for rw in r['rewrites']:
                    lines.append(f"- {rw['title']}: {rw['improvement']}")
                lines.append('')
            if r['plan']:
                lines.append('**执行计划**:')
                icon_map = {'warn': '⚠️', 'error': '❌', 'good': '✅', 'info': 'ℹ️'}
                for p in r['plan']:
                    ic = icon_map.get(p.get('level'), 'ℹ️')
                    lines.append(f"- {ic} {p.get('detail', p.get('raw', ''))}")
                lines.append('')
        report = '\n'.join(lines)
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(report)
        return report


def create_sample_db(db_path):
    random.seed(42)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript('''
    DROP TABLE IF EXISTS orders;
    DROP TABLE IF EXISTS products;
    DROP TABLE IF EXISTS users;
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE,
        age INTEGER,
        city TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT,
        price REAL,
        stock INTEGER DEFAULT 0,
        brand TEXT
    );
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        quantity INTEGER,
        total_price REAL,
        status TEXT DEFAULT 'pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    );
    ''')
    first_names = ['张', '李', '王', '赵', '钱', '孙', '周', '吴', '郑', '陈', '刘', '杨']
    last_names = ['伟', '芳', '娜', '敏', '静', '丽', '强', '磊', '军', '洋', '勇', '艳']
    cities = ['北京', '上海', '广州', '深圳', '杭州', '成都', '武汉', '南京', '西安', '重庆']
    statuses = ['active', 'inactive', 'pending', 'banned']
    users_values = []
    for i in range(1, 1001):
        name = random.choice(first_names) + random.choice(last_names) + str(i)
        email = f"user{i}@example.com"
        age = random.randint(18, 70)
        city = random.choice(cities)
        status = random.choices(statuses, weights=[0.7, 0.1, 0.15, 0.05])[0]
        users_values.append((i, name, email, age, city, status))
    cur.executemany("INSERT INTO users (id, name, email, age, city, status) VALUES (?, ?, ?, ?, ?, ?)", users_values)

    categories = ['电子', '服装', '食品', '家居', '运动', '图书', '美妆', '玩具']
    brands = ['品牌A', '品牌B', '品牌C', '品牌D', '品牌E', '品牌F']
    products_values = []
    for i in range(1, 1001):
        name = f"商品{i}-{random.choice(categories)}类"
        category = random.choice(categories)
        price = round(random.uniform(9.9, 9999.9), 2)
        stock = random.randint(0, 500)
        brand = random.choice(brands)
        products_values.append((i, name, category, price, stock, brand))
    cur.executemany("INSERT INTO products (id, name, category, price, stock, brand) VALUES (?, ?, ?, ?, ?, ?)", products_values)

    order_statuses = ['pending', 'paid', 'shipped', 'completed', 'cancelled', 'refunded']
    orders_values = []
    for i in range(1, 1001):
        user_id = random.randint(1, 1000)
        product_id = random.randint(1, 1000)
        quantity = random.randint(1, 10)
        p_price = products_values[product_id - 1][3]
        total_price = round(p_price * quantity, 2)
        status = random.choice(order_statuses)
        orders_values.append((i, user_id, product_id, quantity, total_price, status))
    cur.executemany("INSERT INTO orders (id, user_id, product_id, quantity, total_price, status) VALUES (?, ?, ?, ?, ?, ?)", orders_values)
    conn.commit()
    conn.close()


SAMPLE_SQLS = [
    "SELECT * FROM users WHERE name LIKE '%张%';",
    "SELECT * FROM users u JOIN orders o ON u.id = o.user_id WHERE u.city = '北京' AND o.status = 'completed' ORDER BY o.created_at DESC;",
    "SELECT u.id, u.name, COUNT(o.id) AS order_count FROM users u LEFT JOIN orders o ON u.id = o.user_id GROUP BY u.id, u.name ORDER BY order_count DESC LIMIT 10;",
    "SELECT * FROM products WHERE price > 1000 AND category = '电子' AND stock > 0;",
    "SELECT * FROM users WHERE id NOT IN (SELECT user_id FROM orders WHERE status = 'cancelled');",
    "SELECT COUNT(*) FROM orders WHERE DATE(created_at) = '2024-01-01';",
    "SELECT u.name, p.name FROM users u, products p WHERE u.id > 100;",
    "SELECT * FROM users WHERE city = '上海' OR age = 25 OR status = 'active';",
    "UPDATE users SET age = 26 WHERE name = '张伟';",
    "SELECT p.category, SUM(o.total_price) AS total FROM orders o JOIN products p ON o.product_id = p.id GROUP BY p.category HAVING total > 10000;",
]


def print_section(title):
    print()
    print("=" * 60)
    print("  " + title)
    print("=" * 60)
    print()


def cmd_parse(args):
    sql = args.sql
    parser = SQLParser(sql)
    result = parser.parse()
    print_section("📋 SQL 解析结果 (树形结构)")
    tree_lines = print_tree(result)
    for line in tree_lines:
        print(line)


def cmd_analyze(args):
    sql = args.sql
    db_path = args.db
    parser = SQLParser(sql)
    parsed = parser.parse()

    print_section("📊 SQL 综合分析")
    print("原 SQL:")
    print("  " + sql)

    print_section("1️⃣  SQL 解析")
    tree_lines = print_tree(parsed)
    for line in tree_lines:
        print(line)

    print_section("2️⃣  索引建议")
    advisor = IndexAdvisor(parsed, db_path)
    indexes = advisor.advise()
    indexes = schema_enhance_indexes(indexes, parsed, db_path)
    if not indexes:
        print("  ✅ 暂无索引建议 (部分可能已有索引覆盖)")
    for idx, sug in enumerate(indexes, 1):
        sev_color = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(sug['severity'], '⚪')
        print(f"  {sev_color} [{idx}] {sug['type']}")
        print(f"     表: {sug['table']} | 列: {', '.join(sug['columns'])}")
        print(f"     原因: {sug['reason']}")
        print(f"     SQL:  {sug['sql']}")
        if idx != len(indexes):
            print()

    schema_enhancements = schema_enhance_analysis(parsed, sql, db_path)
    if schema_enhancements:
        print_section("2️⃣.5  Schema 感知增强分析")
        for idx, enh in enumerate(schema_enhancements, 1):
            sev = {'critical': '🔴 严重', 'high': '🟠 高', 'medium': '🟡 中', 'low': '🟢 低'}.get(enh['severity'], '⚪')
            print(f"  {sev} [{idx}] {enh['type']}")
            print(f"     详情: {enh['detail']}")
            if idx != len(schema_enhancements):
                print()

    print_section("3️⃣  反模式检测")
    detector = AntiPatternDetector(sql, parsed, db_path)
    issues = detector.detect()
    if not issues:
        print("  ✅ 未检测到常见SQL反模式")
    for idx, issue in enumerate(issues, 1):
        sev = {'critical': '🔴 严重', 'high': '🟠 高', 'medium': '🟡 中', 'low': '🟢 低'}.get(issue['severity'], '⚪')
        print(f"  {sev} [{idx}] {issue['pattern']}")
        print(f"     描述: {issue['description']}")
        print(f"     影响: {issue['impact']}")
        print(f"     建议: {issue['suggestion']}")
        if idx != len(issues):
            print()

    print_section("4️⃣  执行计划 (EXPLAIN QUERY PLAN)")
    analyzer = ExplainPlanAnalyzer(sql, db_path)
    plan = analyzer.analyze()
    for p in plan:
        icon = {'warn': '⚠️', 'error': '❌', 'good': '✅', 'info': 'ℹ️'}.get(p.get('level'), 'ℹ️')
        print(f"  {icon} {p.get('detail', p.get('raw', ''))}")
        if 'raw' in p and p['raw'] != p.get('detail'):
            print(f"     [原始] {p['raw']}")

    print_section("5️⃣  SQL 重写建议")
    rewriter = SQLRewriter(sql, parsed, db_path)
    rewrites = rewriter.rewrite()
    if not rewrites:
        print("  ✅ 暂无重写建议")
    for idx, rw in enumerate(rewrites, 1):
        sev = {'high': '🔴', 'medium': '🟡', 'low': '🟢'}.get(rw['severity'], '⚪')
        print(f"  {sev} [{idx}] {rw['title']}")
        print(f"     提升: {rw['improvement']}")
        print(f"     优化前:")
        for line in rw['before'].splitlines():
            print(f"       {line}")
        print(f"     优化后:")
        for line in rw['after'].splitlines():
            print(f"       {line}")
        if idx != len(rewrites):
            print()


def cmd_initdb(args):
    db_path = args.db or 'test.db'
    create_sample_db(db_path)
    print(f"✅ 测试数据库已创建: {db_path}")
    print(f"   - users: 1000 行")
    print(f"   - products: 1000 行")
    print(f"   - orders: 1000 行")


def cmd_batch(args):
    db_path = args.db
    filepath = args.file
    is_log = args.log
    output = args.output
    if not os.path.exists(filepath):
        print(f"❌ 文件不存在: {filepath}")
        return
    analyzer = BatchAnalyzer(db_path)
    print(f"📂 正在分析文件: {filepath}" + (" (日志模式)" if is_log else ""))
    results = analyzer.analyze_file(filepath, is_log)
    print(f"✅ 共分析 {len(results)} 条 SQL")
    if output:
        report = analyzer.generate_markdown_report(results, output)
        print(f"📝 报告已生成: {output}")
    else:
        for r in results:
            print_section(f"SQL #{r['id']} 分析摘要")
            print(f"  SQL: {r['sql'][:100]}{'...' if len(r['sql']) > 100 else ''}")
            if r['issues']:
                print(f"  ❗ 问题数: {len(r['issues'])}")
                for iss in r['issues']:
                    print(f"     - [{iss['severity']}] {iss['pattern']}")
            if r['indexes']:
                print(f"  💡 索引建议: {len(r['indexes'])}")
            if r['rewrites']:
                print(f"  🔧 重写建议: {len(r['rewrites'])}")
        print()
        report = analyzer.generate_markdown_report(results)
        print("=" * 60)
        print("  📄 Markdown 报告预览 (前50行)")
        print("=" * 60)
        for line in report.splitlines()[:50]:
            print(line)
        print()
        print("  使用 --output report.md 可保存完整报告到文件")


def cmd_samples(args):
    print_section("📚 10 条示例 SQL")
    for i, sql in enumerate(SAMPLE_SQLS, 1):
        print(f"  [{i}] {sql}")
        print()


SQLITE_RESERVED_WORDS = {
    'ABORT', 'ACTION', 'ADD', 'AFTER', 'ALL', 'ALTER', 'ALWAYS', 'ANALYZE', 'AND', 'AS', 'ASC',
    'ATTACH', 'AUTOINCREMENT', 'BEFORE', 'BEGIN', 'BETWEEN', 'BY', 'CASCADE', 'CASE', 'CAST',
    'CHECK', 'COLLATE', 'COLUMN', 'COMMIT', 'CONFLICT', 'CONSTRAINT', 'CREATE', 'CROSS',
    'CURRENT', 'CURRENT_DATE', 'CURRENT_TIME', 'CURRENT_TIMESTAMP', 'DATABASE', 'DEFAULT',
    'DEFERRABLE', 'DEFERRED', 'DELETE', 'DESC', 'DETACH', 'DISTINCT', 'DO', 'DROP', 'EACH',
    'ELSE', 'END', 'ESCAPE', 'EXCEPT', 'EXCLUDE', 'EXCLUSIVE', 'EXISTS', 'EXPLAIN', 'FAIL',
    'FILTER', 'FOLLOWING', 'FOR', 'FOREIGN', 'FROM', 'FULL', 'GENERATED', 'GLOB', 'GROUP',
    'GROUPS', 'HAVING', 'IF', 'IGNORE', 'IMMEDIATE', 'IN', 'INDEX', 'INDEXED', 'INITIALLY',
    'INNER', 'INSERT', 'INSTEAD', 'INTERSECT', 'INTO', 'IS', 'ISNULL', 'JOIN', 'KEY', 'LEFT',
    'LIKE', 'LIMIT', 'MATCH', 'NATURAL', 'NO', 'NOT', 'NOTHING', 'NOTNULL', 'NULL', 'NULLS',
    'OF', 'OFFSET', 'ON', 'OR', 'ORDER', 'OTHERS', 'OUTER', 'OVER', 'PARTITION', 'PLAN',
    'PRAGMA', 'PRECEDING', 'PRIMARY', 'QUERY', 'RAISE', 'RANGE', 'RECURSIVE', 'REFERENCES',
    'REGEXP', 'REINDEX', 'RELEASE', 'RENAME', 'REPLACE', 'RESTRICT', 'RETURNING', 'RIGHT',
    'ROLLBACK', 'ROW', 'ROWS', 'SAVEPOINT', 'SELECT', 'SET', 'TABLE', 'TEMP', 'TEMPORARY',
    'THEN', 'TIES', 'TO', 'TRANSACTION', 'TRIGGER', 'UNBOUNDED', 'UNION', 'UNIQUE', 'UPDATE',
    'USING', 'VACUUM', 'VALUES', 'VIEW', 'VIRTUAL', 'WHEN', 'WHERE', 'WINDOW', 'WITH', 'WITHOUT',
}


class ComplexityScorer:
    DIMENSION_WEIGHTS = {
        'table_count': 0.20,
        'join_depth': 0.25,
        'subquery_depth': 0.25,
        'where_count': 0.15,
        'temp_sort': 0.15,
    }

    def __init__(self, sql, parsed=None, db_path=None):
        self.sql = sql
        self.parsed = parsed
        self.db_path = db_path
        if self.parsed is None:
            parser = SQLParser(sql)
            self.parsed = parser.parse()

    def score(self):
        dims = {
            'table_count': self._score_table_count(),
            'join_depth': self._score_join_depth(),
            'subquery_depth': self._score_subquery_depth(),
            'where_count': self._score_where_count(),
            'temp_sort': self._score_temp_sort(),
        }
        total = 0.0
        for k, v in dims.items():
            total += v['score'] * self.DIMENSION_WEIGHTS[k]
        final_score = int(round(100 - total))
        final_score = max(0, min(100, final_score))
        return {
            'score': final_score,
            'grade': self._grade(final_score),
            'dimensions': dims,
        }

    def _grade(self, score):
        if score >= 85:
            return 'A (优秀, 低复杂度)'
        elif score >= 70:
            return 'B (良好, 较低复杂度)'
        elif score >= 55:
            return 'C (一般, 中等复杂度)'
        elif score >= 40:
            return 'D (较差, 高复杂度)'
        else:
            return 'F (很差, 极高复杂度)'

    def _score_table_count(self):
        tables = self.parsed.get('_tables', [])
        joins = self.parsed.get('_joins', [])
        total = len(tables) + len(joins)
        raw = total
        if total <= 1:
            penalty = 0
        elif total == 2:
            penalty = 15
        elif total == 3:
            penalty = 30
        elif total == 4:
            penalty = 50
        elif total == 5:
            penalty = 70
        else:
            penalty = min(100, 70 + (total - 5) * 8)
        return {
            'name': '涉及表数量',
            'value': total,
            'raw': raw,
            'score': penalty,
            'description': f'涉及 {total} 张表' + ('，建议简化关联' if total >= 3 else ''),
        }

    def _score_join_depth(self):
        joins = self.parsed.get('_joins', [])
        depth = len(joins)
        raw = depth
        if depth == 0:
            penalty = 0
        elif depth == 1:
            penalty = 10
        elif depth == 2:
            penalty = 25
        elif depth == 3:
            penalty = 45
        elif depth == 4:
            penalty = 65
        else:
            penalty = min(100, 65 + (depth - 4) * 10)
        return {
            'name': 'JOIN 层数',
            'value': depth,
            'raw': raw,
            'score': penalty,
            'description': f'{depth} 层 JOIN' + ('，JOIN 过深影响性能' if depth >= 3 else ''),
        }

    def _score_subquery_depth(self):
        depth = self._calc_subquery_depth(self.sql)
        raw = depth
        if depth == 0:
            penalty = 0
        elif depth == 1:
            penalty = 15
        elif depth == 2:
            penalty = 40
        elif depth == 3:
            penalty = 70
        else:
            penalty = min(100, 70 + (depth - 3) * 10)
        return {
            'name': '子查询嵌套深度',
            'value': depth,
            'raw': raw,
            'score': penalty,
            'description': f'嵌套 {depth} 层子查询' + ('，建议改写为 JOIN' if depth >= 2 else ''),
        }

    def _calc_subquery_depth(self, sql):
        tokens = tokenize_sql(sql)
        max_depth = 0
        current = 0
        i = 0
        while i < len(tokens):
            if tokens[i] == '(':
                if i + 1 < len(tokens) and tokens[i + 1].upper() == 'SELECT':
                    current += 1
                    max_depth = max(max_depth, current)
            elif tokens[i] == ')':
                if current > 0:
                    end = find_matching_paren(tokens, i - 1)
                    if end == i:
                        current -= 1
            i += 1
        return max_depth

    def _score_where_count(self):
        conds = [c for c in self.parsed.get('_where_conditions', [])
                 if not c.startswith('逻辑符:')]
        having = [c for c in self.parsed.get('_having', []) if not c.startswith('逻辑符:')]
        total = len(conds) + len(having)
        raw = total
        if total <= 2:
            penalty = 0
        elif total <= 4:
            penalty = 15
        elif total <= 6:
            penalty = 35
        elif total <= 8:
            penalty = 55
        else:
            penalty = min(100, 55 + (total - 8) * 5)
        return {
            'name': 'WHERE 条件数量',
            'value': total,
            'raw': raw,
            'score': penalty,
            'description': f'{total} 个条件' + ('，复杂条件需注意索引设计' if total >= 4 else ''),
        }

    def _score_temp_sort(self):
        has_temp = False
        reasons = []
        order_by = self.parsed.get('_order_by', [])
        group_by = self.parsed.get('_group_by', [])
        distinct = self.parsed.get('_distinct', False)
        if distinct and not order_by and not group_by:
            has_temp = True
            reasons.append('DISTINCT 去重需要临时 B-Tree')
        if order_by and group_by:
            has_temp = True
            reasons.append('同时有 ORDER BY 和 GROUP BY')
        if len(group_by) >= 2:
            has_temp = True
            reasons.append('多列 GROUP BY')
        if len(order_by) >= 2:
            has_temp = True
            reasons.append('多列 ORDER BY')
        if self.db_path and os.path.exists(self.db_path):
            try:
                analyzer = ExplainPlanAnalyzer(self.sql, self.db_path)
                plan = analyzer.analyze()
                for p in plan:
                    if 'TEMP B-TREE' in p.get('raw', '').upper():
                        has_temp = True
                        reasons.append(p.get('detail', '使用临时 B-Tree'))
                        break
            except:
                pass
        raw = 1 if has_temp else 0
        penalty = 60 if has_temp else 0
        desc = '; '.join(reasons) if reasons else ('使用临时表排序' if has_temp else '无临时表排序开销')
        return {
            'name': '临时表排序',
            'value': has_temp,
            'raw': raw,
            'score': penalty,
            'description': desc,
        }


class SchemaManager:
    CACHE_FILE = '.sqlopt_schema.json'

    def __init__(self, db_path):
        self.db_path = db_path
        self.cache_path = os.path.join(os.path.dirname(os.path.abspath(db_path)) if os.path.dirname(db_path) else '.', self.CACHE_FILE)
        self.schema = None
        self._load()

    def _db_mtime(self):
        if not os.path.exists(self.db_path):
            return 0
        return os.path.getmtime(self.db_path)

    def _cache_mtime(self):
        if not os.path.exists(self.cache_path):
            return 0
        return os.path.getmtime(self.cache_path)

    def _load(self):
        db_mtime = self._db_mtime()
        cache_mtime = self._cache_mtime()
        if cache_mtime > 0 and cache_mtime >= db_mtime and db_mtime > 0:
            try:
                with open(self.cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get('db_path') == os.path.abspath(self.db_path):
                    self.schema = data.get('schema', {})
                    return
            except:
                pass
        self._extract()
        self._save_cache()

    def refresh(self):
        self._extract()
        self._save_cache()

    def _save_cache(self):
        try:
            with open(self.cache_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'db_path': os.path.abspath(self.db_path),
                    'db_mtime': self._db_mtime(),
                    'schema': self.schema,
                }, f, ensure_ascii=False, indent=2)
        except:
            pass

    def _extract(self):
        self.schema = {}
        if not os.path.exists(self.db_path):
            return
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            tables = [r[0] for r in cur.fetchall()]
            for table in tables:
                table_info = {'name': table, 'columns': [], 'indexes': [], 'foreign_keys': []}
                cur.execute(f'PRAGMA table_info("{table}")')
                for col in cur.fetchall():
                    cid, name, col_type, notnull, default, pk = col
                    table_info['columns'].append({
                        'cid': cid,
                        'name': name,
                        'type': col_type,
                        'not_null': bool(notnull),
                        'default': default,
                        'primary_key': bool(pk),
                    })
                cur.execute(f'PRAGMA foreign_key_list("{table}")')
                for fk in cur.fetchall():
                    table_info['foreign_keys'].append({
                        'id': fk[0],
                        'seq': fk[1],
                        'table': fk[2],
                        'from': fk[3],
                        'to': fk[4],
                    })
                cur.execute(f'PRAGMA index_list("{table}")')
                indexes_raw = cur.fetchall()
                for idx in indexes_raw:
                    idx_name = idx[1]
                    idx_unique = bool(idx[2])
                    idx_origin = idx[3]
                    if idx_origin == 'pk':
                        continue
                    cur.execute(f'PRAGMA index_info("{idx_name}")')
                    idx_cols = [c[2] for c in cur.fetchall()]
                    table_info['indexes'].append({
                        'name': idx_name,
                        'unique': idx_unique,
                        'columns': idx_cols,
                    })
                self.schema[table] = table_info
            conn.close()
        except Exception as e:
            self.schema = {'_error': str(e)}

    def get_table(self, table_name):
        if not self.schema:
            return None
        for name, info in self.schema.items():
            if name.lower() == table_name.lower():
                return info
        return None

    def get_column(self, table_name, column_name):
        table = self.get_table(table_name)
        if not table:
            return None
        for col in table['columns']:
            if col['name'].lower() == column_name.lower():
                return col
        return None

    def column_has_index(self, table_name, column_name):
        table = self.get_table(table_name)
        if not table:
            return False
        col_lower = column_name.lower()
        for idx in table.get('indexes', []):
            if idx['columns'] and idx['columns'][0].lower() == col_lower:
                return True
        for col in table.get('columns', []):
            if col['primary_key'] and col['name'].lower() == col_lower:
                return True
        return False

    def get_index_for_columns(self, table_name, columns):
        table = self.get_table(table_name)
        if not table:
            return None
        cols_lower = [c.lower() for c in columns]
        for idx in table.get('indexes', []):
            idx_cols_lower = [c.lower() for c in idx['columns']]
            if idx_cols_lower[:len(cols_lower)] == cols_lower:
                return idx
        if len(columns) == 1:
            for col in table.get('columns', []):
                if col['primary_key'] and col['name'].lower() == cols_lower[0]:
                    return {'name': f'{table_name}_pkey', 'unique': True, 'columns': [columns[0]]}
        return None

    def get_join_column_types(self, left_table, left_col, right_table, right_col):
        lc = self.get_column(left_table, left_col)
        rc = self.get_column(right_table, right_col)
        ltype = lc['type'] if lc else None
        rtype = rc['type'] if rc else None
        return ltype, rtype

    def is_type_match(self, type1, type2):
        if not type1 or not type2:
            return True
        t1 = type1.upper().strip()
        t2 = type2.upper().strip()
        if t1 == t2:
            return True
        int_family = {'INT', 'INTEGER', 'BIGINT', 'SMALLINT', 'TINYINT', 'MEDIUMINT'}
        text_family = {'TEXT', 'VARCHAR', 'CHAR', 'NVARCHAR', 'NCHAR', 'CLOB', 'STRING'}
        real_family = {'REAL', 'FLOAT', 'DOUBLE', 'DECIMAL', 'NUMERIC'}
        blob_family = {'BLOB', 'BINARY', 'VARBINARY'}
        families = [int_family, text_family, real_family, blob_family]
        for fam in families:
            if any(t1.startswith(f) or f in t1 for f in fam):
                if any(t2.startswith(f) or f in t2 for f in fam):
                    return True
                return False
        return True

    def is_not_null(self, table_name, column_name):
        col = self.get_column(table_name, column_name)
        if col:
            return col['not_null']
        return False


def schema_enhance_analysis(parsed, sql, db_path):
    enhancements = []
    if not db_path or not os.path.exists(db_path):
        return enhancements
    schema_mgr = SchemaManager(db_path)
    tables = parsed.get('_tables', [])
    joins = parsed.get('_joins', [])
    where_conds = parsed.get('_where_conditions', [])
    all_tables = []
    for t in tables:
        base = t.split()[0].split(' AS ')[0].strip('`"')
        alias = None
        if ' AS ' in t.upper():
            alias = t.split(' AS ')[1].strip().strip('`"').split()[0]
        elif len(t.split()) >= 2:
            alias = t.split()[-1].strip('`"')
        all_tables.append({'base': base, 'alias': alias or base})
    for j in joins:
        tbl = j['table'].split()[0].split(' AS ')[0].strip('`"')
        alias = None
        if ' AS ' in j['table'].upper():
            alias = j['table'].split(' AS ')[1].strip().strip('`"').split()[0]
        elif len(j['table'].split()) >= 2:
            alias = j['table'].split()[-1].strip('`"')
        all_tables.append({'base': tbl, 'alias': alias or tbl})

    def resolve_table(col):
        if '.' in col:
            prefix = col.split('.')[0].strip('`"')
            for t in all_tables:
                if t['alias'].lower() == prefix.lower() or t['base'].lower() == prefix.lower():
                    return t['base'], col.split('.', 1)[1].strip('`"')
        col_simple = col.strip('`"')
        for t in all_tables:
            if schema_mgr.get_column(t['base'], col_simple):
                return t['base'], col_simple
        if all_tables:
            return all_tables[0]['base'], col_simple
        return None, col_simple

    for j in joins:
        for cond in j.get('conditions', []):
            if cond.startswith('逻辑符:'):
                continue
            advisor = IndexAdvisor.__new__(IndexAdvisor)
            advisor.parsed = parsed
            col, op, val = advisor._parse_condition(cond)
            if col and op == '=' and val and '.' in col and '.' in val:
                left_table, left_col = resolve_table(col)
                right_table, right_col = resolve_table(val)
                if left_table and right_table:
                    ltype, rtype = schema_mgr.get_join_column_types(left_table, left_col, right_table, right_col)
                    if ltype and rtype and not schema_mgr.is_type_match(ltype, rtype):
                        enhancements.append({
                            'severity': 'high',
                            'type': '隐式类型转换风险',
                            'detail': f'JOIN 列类型不匹配: {left_table}.{left_col} ({ltype}) vs {right_table}.{right_col} ({rtype})',
                        })
    for cond in where_conds:
        if cond.startswith('逻辑符:'):
            continue
        advisor = IndexAdvisor.__new__(IndexAdvisor)
        advisor.parsed = parsed
        col, op, val = advisor._parse_condition(cond)
        if not col:
            continue
        tbl, col_simple = resolve_table(col)
        if not tbl:
            continue
        if op in ('IS', 'IS NOT'):
            if val and ('NULL' in val.upper()):
                if schema_mgr.is_not_null(tbl, col_simple):
                    enhancements.append({
                        'severity': 'low',
                        'type': '冗余 IS NULL 检查',
                        'detail': f'{tbl}.{col_simple} 为 NOT NULL 列，无需 IS NULL 检查',
                    })
    return enhancements


def schema_enhance_indexes(suggestions, parsed, db_path):
    if not db_path or not os.path.exists(db_path):
        return suggestions
    schema_mgr = SchemaManager(db_path)
    filtered = []
    for sug in suggestions:
        tbl = sug['table'].split()[0].split(' AS ')[0].strip('`"')
        cols = sug['columns']
        existing = schema_mgr.get_index_for_columns(tbl, cols)
        if existing:
            continue
        filtered.append(sug)
    return filtered


class SQLFormatter:
    CLAUSE_ORDER = [
        'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'HAVING',
        'WINDOW', 'UNION', 'UNION ALL', 'INTERSECT', 'EXCEPT',
        'ORDER BY', 'LIMIT', 'OFFSET',
    ]

    def __init__(self, sql, keyword_case='upper', indent='    ', comma_position='leading'):
        self.sql = sql.strip().rstrip(';')
        self.keyword_case = keyword_case
        self.indent = indent
        self.comma_position = comma_position

    def format(self):
        tokens = self._tokenize_formatted(self.sql)
        if not tokens:
            return ''
        lines = self._build_lines(tokens)
        result = '\n'.join(lines) + ';'
        return result

    def _tokenize_formatted(self, sql):
        tokens = []
        buf = []
        i = 0
        in_string = False
        string_char = None
        while i < len(sql):
            ch = sql[i]
            if in_string:
                buf.append(ch)
                if ch == string_char:
                    if i + 1 < len(sql) and sql[i + 1] == string_char:
                        buf.append(sql[i + 1])
                        i += 1
                    else:
                        in_string = False
            else:
                if ch in ("'", '"', '`'):
                    if buf:
                        tokens.append(('id', ''.join(buf)))
                        buf = []
                    in_string = True
                    string_char = ch
                    buf = [ch]
                elif ch in '(),':
                    if buf:
                        tokens.append(('id', ''.join(buf)))
                        buf = []
                    tokens.append(('punct', ch))
                elif ch.isspace():
                    if buf:
                        tokens.append(('id', ''.join(buf)))
                        buf = []
                elif ch in '=<>!+-*/%':
                    if buf:
                        tokens.append(('id', ''.join(buf)))
                        buf = []
                    j = i
                    while j < len(sql) and sql[j] in '=<>!+-*/%':
                        j += 1
                    tokens.append(('op', sql[i:j]))
                    i = j - 1
                else:
                    buf.append(ch)
            i += 1
        if buf:
            tokens.append(('id', ''.join(buf)))
        processed = []
        for ttype, tval in tokens:
            if ttype == 'id' and tval.upper() in SQL_KEYWORDS:
                if self.keyword_case == 'upper':
                    processed.append(('kw', tval.upper()))
                elif self.keyword_case == 'lower':
                    processed.append(('kw', tval.lower()))
                else:
                    processed.append(('kw', tval))
            else:
                processed.append((ttype, tval))
        return processed

    def _build_lines(self, tokens):
        lines = []
        current = []
        depth = 0
        paren_depth = 0
        i = 0
        new_clause_keywords = {'SELECT', 'FROM', 'WHERE', 'GROUP', 'HAVING', 'WINDOW', 'UNION', 'INTERSECT', 'EXCEPT', 'ORDER', 'LIMIT', 'OFFSET', 'VALUES', 'SET'}

        def flush():
            nonlocal current
            if current:
                line = self.indent * max(0, depth - 1) + ' '.join(current)
                if line.strip():
                    lines.append(line)
                current = []

        while i < len(tokens):
            ttype, tval = tokens[i]
            if ttype == 'kw':
                if tval == 'BY' and i > 0 and tokens[i - 1][1].upper() in ('GROUP', 'ORDER', 'PARTITION', 'WINDOW'):
                    current.append(tval)
                    flush()
                    i += 1
                    continue
                if tval == 'ALL' and i > 0 and tokens[i - 1][1].upper() == 'UNION':
                    current[-1] = current[-1] + ' ' + tval
                    i += 1
                    continue
                if tval.upper() in new_clause_keywords:
                    flush()
                    depth = 1 if tval.upper() in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'WITH') else max(1, depth)
                    clause_kw = tval
                    if tval.upper() in ('GROUP', 'ORDER') and i + 1 < len(tokens) and tokens[i + 1][1].upper() == 'BY':
                        clause_kw = tval + ' BY'
                    current.append(clause_kw)
                    flush()
                    depth = 2
                    if tval.upper() in ('GROUP', 'ORDER'):
                        i += 2
                    else:
                        i += 1
                    continue
                if tval.upper() in ('LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'NATURAL', 'FULL'):
                    join_parts = [tval]
                    j = i + 1
                    while j < len(tokens) and tokens[j][1].upper() in ('LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'NATURAL', 'FULL', 'JOIN'):
                        join_parts.append(tokens[j][1])
                        j += 1
                        if join_parts[-1].upper() == 'JOIN':
                            break
                    flush()
                    current.append(' '.join(join_parts))
                    i = j
                    continue
                if tval.upper() == 'JOIN':
                    flush()
                    current.append(tval)
                    i += 1
                    continue
                if tval.upper() in ('AND', 'OR'):
                    flush()
                    current.append(tval)
                    i += 1
                    continue
                if tval.upper() == 'AS':
                    current.append(tval)
                    i += 1
                    continue
            if ttype == 'punct':
                if tval == '(':
                    current.append(tval)
                    paren_depth += 1
                    depth += 1
                    i += 1
                    continue
                if tval == ')':
                    flush()
                    paren_depth -= 1
                    depth = max(1, depth - 1)
                    if self.comma_position == 'leading':
                        line = self.indent * max(0, depth - 1) + tval
                    else:
                        line = (self.indent * max(0, depth - 1) + tval) if lines else tval
                    lines.append(line)
                    i += 1
                    continue
                if tval == ',':
                    if self.comma_position == 'trailing':
                        current.append(tval)
                        flush()
                    else:
                        flush()
                        current.append(tval)
                    i += 1
                    continue
            current.append(tval)
            i += 1
        flush()
        return lines


class SQLLinter:
    DEFAULT_RULES = {
        'snake_case_table': True,
        'snake_case_column': True,
        'avoid_reserved_words': True,
        'join_must_have_on': True,
        'meaningful_alias': True,
        'select_star': True,
        'no_trailing_whitespace': True,
    }
    RULE_DESCRIPTIONS = {
        'snake_case_table': '表名应使用小写蛇形命名',
        'snake_case_column': '列名应使用小写蛇形命名',
        'avoid_reserved_words': '避免使用保留字作为列名或表名',
        'join_must_have_on': 'JOIN 必须有 ON 条件',
        'meaningful_alias': '别名应有意义，避免单字母别名',
        'select_star': '避免使用 SELECT *',
        'no_trailing_whitespace': '避免行尾空格',
    }

    def __init__(self, sql, rules=None, config_path='.sqlopt.yaml'):
        self.sql = sql
        self.rules = dict(self.DEFAULT_RULES)
        if rules:
            self.rules.update(rules)
        self._load_config(config_path)
        self.issues = []
        try:
            self.parser = SQLParser(sql)
            self.parsed = self.parser.parse()
        except:
            self.parsed = None

    def _load_config(self, config_path):
        if not os.path.exists(config_path):
            return
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self._parse_simple_yaml(content)
        except:
            pass

    def _parse_simple_yaml(self, content):
        in_rules = False
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if stripped == 'rules:':
                in_rules = True
                continue
            if in_rules and stripped.startswith('- '):
                rule = stripped[2:].strip()
                if rule.startswith('no_'):
                    rname = rule[3:]
                    if rname in self.rules:
                        self.rules[rname] = False
                else:
                    rname = rule
                    if rname in self.rules:
                        self.rules[rname] = True
            elif ':' in stripped and not stripped.startswith('-'):
                k, v = stripped.split(':', 1)
                k = k.strip()
                v = v.strip().lower()
                if k in self.rules:
                    self.rules[k] = v in ('true', 'yes', 'on', '1')

    def lint(self):
        if self.rules.get('select_star'):
            self._check_select_star()
        if self.rules.get('join_must_have_on'):
            self._check_join_on()
        if self.rules.get('meaningful_alias'):
            self._check_alias()
        if self.rules.get('avoid_reserved_words'):
            self._check_reserved_words()
        if self.rules.get('snake_case_table'):
            self._check_snake_case_tables()
        if self.rules.get('snake_case_column'):
            self._check_snake_case_columns()
        if self.rules.get('no_trailing_whitespace'):
            self._check_trailing_space()
        return self.issues

    def _check_select_star(self):
        if not self.parsed:
            return
        cols = self.parsed.get('_select_cols', [])
        if '*' in cols:
            self.issues.append({
                'rule': 'select_star',
                'severity': 'high',
                'line': self._find_line('SELECT') + 1,
                'message': self.RULE_DESCRIPTIONS['select_star'],
            })

    def _check_join_on(self):
        if not self.parsed:
            return
        joins = self.parsed.get('_joins', [])
        for idx, j in enumerate(joins):
            if not j.get('conditions'):
                self.issues.append({
                    'rule': 'join_must_have_on',
                    'severity': 'critical',
                    'line': self._find_line(j['type']) + 1,
                    'message': f'{j["type"]} 缺少 ON 条件: {j["table"]}',
                })

    def _check_alias(self):
        if not self.parsed:
            return
        tables = self.parsed.get('_tables', []) + [j['table'] for j in self.parsed.get('_joins', [])]
        for t in tables:
            parts = t.split()
            alias = None
            if ' AS ' in t.upper():
                alias = t.split(' AS ')[1].strip().strip('`"').split()[0]
            elif len(parts) >= 2:
                alias = parts[-1].strip('`"')
            if alias and len(alias) == 1 and alias.isalpha():
                self.issues.append({
                    'rule': 'meaningful_alias',
                    'severity': 'low',
                    'line': self._find_line(alias) + 1,
                    'message': f'别名 "{alias}" 过于简短，建议使用有意义的别名',
                })

    def _check_reserved_words(self):
        if not self.parsed:
            return
        identifiers = set()
        tables = self.parsed.get('_tables', []) + [j['table'] for j in self.parsed.get('_joins', [])]
        for t in tables:
            base = t.split()[0].split(' AS ')[0].strip('`"')
            identifiers.add(base)
            if ' AS ' in t.upper():
                alias = t.split(' AS ')[1].strip().strip('`"').split()[0]
                identifiers.add(alias)
            elif len(t.split()) >= 2:
                alias = t.split()[-1].strip('`"')
                identifiers.add(alias)
        if self.parsed.get('_table'):
            identifiers.add(self.parsed['_table'])
        for c in self.parsed.get('_select_cols', []):
            if c == '*':
                continue
            clean = c.split('.')[-1].strip('`"() ').strip()
            if clean and re.match(r'^[a-zA-Z_]\w*$', clean):
                identifiers.add(clean)
        where = [c for c in self.parsed.get('_where_conditions', []) if not c.startswith('逻辑符:')]
        for w in where:
            for part in re.split(r'[=<>!+\-*/%]', w):
                p = part.strip().split('.')[-1].strip('`" ').strip()
                if p and re.match(r'^[a-zA-Z_]\w*$', p):
                    identifiers.add(p)
        for ident in identifiers:
            upper = ident.upper()
            if upper in SQLITE_RESERVED_WORDS:
                self.issues.append({
                    'rule': 'avoid_reserved_words',
                    'severity': 'medium',
                    'line': self._find_line(ident) + 1,
                    'message': f'"{ident}" 是 SQLite 保留字，建议加反引号或改名',
                })

    def _check_snake_case_tables(self):
        if not self.parsed:
            return
        tables = []
        for t in self.parsed.get('_tables', []):
            tables.append(t.split()[0].split(' AS ')[0].strip('`"'))
        for j in self.parsed.get('_joins', []):
            tables.append(j['table'].split()[0].split(' AS ')[0].strip('`"'))
        if self.parsed.get('_table'):
            tables.append(self.parsed['_table'])
        for t in tables:
            if t.startswith('sqlite_'):
                continue
            if t != t.lower() or ' ' in t:
                self.issues.append({
                    'rule': 'snake_case_table',
                    'severity': 'medium',
                    'line': self._find_line(t) + 1,
                    'message': f'表名 "{t}" 不符合小写蛇形命名规范',
                })
            elif not re.match(r'^[a-z][a-z0-9_]*$', t):
                self.issues.append({
                    'rule': 'snake_case_table',
                    'severity': 'medium',
                    'line': self._find_line(t) + 1,
                    'message': f'表名 "{t}" 应使用小写蛇形命名 (lower_snake_case)',
                })

    def _check_snake_case_columns(self):
        if not self.parsed:
            return
        cols = []
        for c in self.parsed.get('_select_cols', []):
            if c == '*':
                continue
            clean = c.split('.')[-1].strip('`"() ').strip()
            if clean and not clean.startswith('COUNT') and not clean.startswith('SUM') and not clean.startswith('AVG') and not clean.startswith('MIN') and not clean.startswith('MAX'):
                cols.append(clean)
        where = [c for c in self.parsed.get('_where_conditions', []) if not c.startswith('逻辑符:')]
        for w in where:
            for part in re.split(r'[=<>!+\-*/%]', w):
                p = part.strip().split('.')[-1].strip('`" ').strip()
                if p and re.match(r'^[a-zA-Z_]', p):
                    cols.append(p)
        for c in cols:
            if not c or not re.match(r'^[a-zA-Z_]', c):
                continue
            if c != c.lower() or ' ' in c:
                self.issues.append({
                    'rule': 'snake_case_column',
                    'severity': 'low',
                    'line': self._find_line(c) + 1,
                    'message': f'列名 "{c}" 不符合小写蛇形命名规范',
                })

    def _check_trailing_space(self):
        for idx, line in enumerate(self.sql.splitlines()):
            if line != line.rstrip():
                self.issues.append({
                    'rule': 'no_trailing_whitespace',
                    'severity': 'info',
                    'line': idx + 1,
                    'message': f'第 {idx + 1} 行有行尾空格',
                })

    def _find_line(self, keyword):
        upper_sql = self.sql.upper()
        upper_kw = keyword.upper()
        pos = upper_sql.find(upper_kw)
        if pos < 0:
            return 0
        return self.sql.count('\n', 0, pos)

    def fix(self):
        fixed_sql = self.sql
        if self.rules.get('select_star') and self.parsed:
            pass
        if self.rules.get('no_trailing_whitespace'):
            lines = fixed_sql.splitlines()
            lines = [l.rstrip() for l in lines]
            fixed_sql = '\n'.join(lines)
        return fixed_sql


def cmd_score(args):
    as_json = args.json
    file_mode = args.file
    if file_mode:
        if not os.path.exists(file_mode):
            print(f"❌ 文件不存在: {file_mode}")
            return
        with open(file_mode, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        sqls = split_sql_statements(content)
        if not sqls:
            print("❌ 文件中未找到SQL语句")
            return
        print(f"📂 正在分析 {len(sqls)} 条 SQL，按复杂度输出 TOP10...")
        results = []
        for idx, sql in enumerate(sqls, 1):
            try:
                scorer = ComplexityScorer(sql, db_path=args.db)
                r = scorer.score()
                results.append({'id': idx, 'sql': sql, 'result': r})
            except:
                pass
        results.sort(key=lambda x: x['result']['score'])
        top10 = results[:10]
        if as_json:
            output = {
                'total': len(results),
                'top10': [
                    {
                        'rank': i + 1,
                        'id': r['id'],
                        'score': r['result']['score'],
                        'grade': r['result']['grade'],
                        'sql': r['sql'][:200],
                        'dimensions': {k: v['score'] for k, v in r['result']['dimensions'].items()},
                    }
                    for i, r in enumerate(top10)
                ],
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return
        for i, r in enumerate(top10, 1):
            s = r['result']
            print()
            print(f"  🏆 TOP {i} (SQL #{r['id']})  评分: {s['score']}  {s['grade']}")
            print(f"     SQL: {r['sql'][:120]}{'...' if len(r['sql']) > 120 else ''}")
            for dim_name, dim in s['dimensions'].items():
                bar_len = int(dim['score'] / 5)
                bar = '█' * bar_len + '░' * (20 - bar_len)
                print(f"     ⚙ {dim['name']:<14} |{bar}| {dim['score']:3d}分  {dim['description']}")
        return
    sql = args.sql
    scorer = ComplexityScorer(sql, db_path=args.db)
    result = scorer.score()
    if as_json:
        output = {
            'score': result['score'],
            'grade': result['grade'],
            'sql': sql,
            'dimensions': {
                k: {'name': v['name'], 'value': v['value'], 'score': v['score'], 'description': v['description']}
                for k, v in result['dimensions'].items()
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    print_section("📊 SQL 复杂度评分")
    print(f"  原 SQL: {sql[:120]}{'...' if len(sql) > 120 else ''}")
    print()
    score_bar_len = int(result['score'] / 5)
    score_bar = '█' * score_bar_len + '░' * (20 - score_bar_len)
    print(f"  综合评分: |{score_bar}|  {result['score']} 分")
    print(f"  等级: {result['grade']}")
    print()
    print("  各维度评分:")
    for dim_key, dim in result['dimensions'].items():
        w = ComplexityScorer.DIMENSION_WEIGHTS[dim_key]
        bar_len = int(dim['score'] / 5)
        bar = '█' * bar_len + '░' * (20 - bar_len)
        print(f"    ⚙ {dim['name']:<14} (权重 {w*100:0.0f}%) |{bar}| 扣除 {dim['score']:3d}分  {dim['description']}")


def cmd_diff(args):
    sql1 = args.original
    sql2 = args.optimized
    as_json = args.json
    scorer1 = ComplexityScorer(sql1)
    scorer2 = ComplexityScorer(sql2)
    r1 = scorer1.score()
    r2 = scorer2.score()
    diff_score = r2['score'] - r1['score']
    if as_json:
        output = {
            'original': {'score': r1['score'], 'grade': r1['grade'],
                         'dimensions': {k: v['score'] for k, v in r1['dimensions'].items()}},
            'optimized': {'score': r2['score'], 'grade': r2['grade'],
                          'dimensions': {k: v['score'] for k, v in r2['dimensions'].items()}},
            'diff_score': diff_score,
            'improved': diff_score > 0,
            'dimension_diffs': {
                k: {'original': r1['dimensions'][k]['score'],
                    'optimized': r2['dimensions'][k]['score'],
                    'diff': r2['dimensions'][k]['score'] - r1['dimensions'][k]['score'],
                    'improved': r2['dimensions'][k]['score'] < r1['dimensions'][k]['score']}
                for k in r1['dimensions']
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return
    print_section("📊 SQL 复杂度对比")
    print(f"  原始:  {r1['score']} 分  {r1['grade']}")
    print(f"  优化后: {r2['score']} 分  {r2['grade']}")
    diff_symbol = '✅' if diff_score > 0 else ('⚠️' if diff_score == 0 else '❌')
    print(f"  {diff_symbol} 评分差异: {diff_score:+d} 分 {'(提升)' if diff_score > 0 else ('(无变化)' if diff_score == 0 else '(下降)')}")
    print()
    print("  各维度对比:")
    for dim_key in r1['dimensions']:
        d1 = r1['dimensions'][dim_key]
        d2 = r2['dimensions'][dim_key]
        diff = d2['score'] - d1['score']
        if diff < 0:
            icon = '📗'
        elif diff > 0:
            icon = '📕'
        else:
            icon = '📘'
        improved_tag = ' ✨ 改进' if diff < 0 else (' 退步' if diff > 0 else ' 无变化')
        print(f"  {icon} {d1['name']:<14}  原始: -{d1['score']:3d}分  优化后: -{d2['score']:3d}分  差异: {diff:+d}分{improved_tag}")
    print()
    improved_dims = [k for k in r1['dimensions'] if r2['dimensions'][k]['score'] < r1['dimensions'][k]['score']]
    if improved_dims:
        print("  ✨ 改进的维度:")
        for k in improved_dims:
            d1 = r1['dimensions'][k]
            d2 = r2['dimensions'][k]
            print(f"     • {d1['name']}: {d1['description']}  →  {d2['description']}")


def cmd_schema(args):
    db_path = args.db
    if not db_path or not os.path.exists(db_path):
        print(f"❌ 数据库不存在: {db_path}")
        return
    if args.refresh:
        mgr = SchemaManager.__new__(SchemaManager)
        mgr.db_path = db_path
        mgr.cache_path = os.path.join(os.path.dirname(os.path.abspath(db_path)) or '.', SchemaManager.CACHE_FILE)
        mgr.schema = None
        mgr._extract()
        mgr._save_cache()
        print(f"✅ Schema 缓存已刷新: {mgr.cache_path}")
    mgr = SchemaManager(db_path)
    as_json = args.json
    if as_json:
        print(json.dumps(mgr.schema, ensure_ascii=False, indent=2))
        return
    print_section(f"🗄  数据库 Schema 信息: {db_path}")
    if not mgr.schema:
        print("  ❌ 未提取到任何表信息")
        return
    for table_name, info in mgr.schema.items():
        if table_name.startswith('_'):
            continue
        print(f"  📋 表: {table_name}")
        print(f"     {'列名':<25} {'类型':<15} {'主键':<6} {'非空':<6} 默认值")
        print(f"     {'-'*25} {'-'*15} {'-'*6} {'-'*6} {'-'*15}")
        for col in info['columns']:
            pk = '✅' if col['primary_key'] else ''
            nn = '✅' if col['not_null'] else ''
            dval = str(col['default']) if col['default'] is not None else ''
            print(f"     {col['name']:<25} {col['type']:<15} {pk:<6} {nn:<6} {dval}")
        if info.get('indexes'):
            print()
            print(f"     🔍 索引:")
            for idx in info['indexes']:
                uniq = '(唯一)' if idx['unique'] else ''
                print(f"       • {idx['name']} {uniq}: ({', '.join(idx['columns'])})")
        if info.get('foreign_keys'):
            print()
            print(f"     🔗 外键:")
            for fk in info['foreign_keys']:
                print(f"       • {fk['from']} → {fk['table']}.{fk['to']}")
        print()


def cmd_format(args):
    sql = None
    file_mode = args.file
    fix_mode = args.fix
    style = args.style
    comma = args.comma
    indent_str = ' ' * (args.indent or 4)
    formatter_kwargs = {
        'keyword_case': style,
        'indent': indent_str,
        'comma_position': comma,
    }
    if file_mode:
        if not os.path.exists(file_mode):
            print(f"❌ 文件不存在: {file_mode}")
            return
        with open(file_mode, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        sqls = split_sql_statements(content)
        formatted_parts = []
        for s in sqls:
            fm = SQLFormatter(s, **formatter_kwargs)
            formatted_parts.append(fm.format())
        result = '\n\n'.join(formatted_parts)
        if fix_mode:
            bak = file_mode + '.bak'
            if not os.path.exists(bak):
                with open(bak, 'w', encoding='utf-8') as f:
                    f.write(content)
            with open(file_mode, 'w', encoding='utf-8') as f:
                f.write(result + '\n')
            print(f"✅ 已格式化 {file_mode} (备份: {bak})")
        else:
            print(result)
        return
    sql = args.sql
    fm = SQLFormatter(sql, **formatter_kwargs)
    result = fm.format()
    print(result)


def cmd_lint(args):
    sql = None
    file_mode = args.file
    fix_mode = args.fix
    config = args.config or '.sqlopt.yaml'
    if file_mode:
        if not os.path.exists(file_mode):
            print(f"❌ 文件不存在: {file_mode}")
            return
        with open(file_mode, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        sqls = split_sql_statements(content)
        all_issues = []
        offset = 0
        for idx, s in enumerate(sqls, 1):
            ln = SQLLinter(s, config_path=config)
            issues = ln.lint()
            for iss in issues:
                iss['sql_id'] = idx
            all_issues.extend(issues)
            offset += s.count('\n') + 2
        if fix_mode:
            fixed_sqls = []
            for s in sqls:
                ln = SQLLinter(s, config_path=config)
                ln.lint()
                fixed_sqls.append(ln.fix())
            result = '\n\n'.join(fixed_sqls)
            bak = file_mode + '.bak'
            if not os.path.exists(bak):
                with open(bak, 'w', encoding='utf-8') as f:
                    f.write(content)
            with open(file_mode, 'w', encoding='utf-8') as f:
                f.write(result + '\n')
            print(f"✅ 已自动修复可修复问题: {file_mode} (备份: {bak})")
        print_section(f"🔍 Lint 检查: {file_mode}")
        if not all_issues:
            print("  ✅ 未发现规范问题")
            return
        sev_count = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
        for iss in all_issues:
            sev_count[iss['severity']] = sev_count.get(iss['severity'], 0) + 1
        print(f"  共发现 {len(all_issues)} 个问题: 🔴严重{sev_count['critical']} | 🟠高{sev_count['high']} | 🟡中{sev_count['medium']} | 🟢低{sev_count['low']} | ℹ️信息{sev_count['info']}")
        print()
        sev_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}
        all_issues.sort(key=lambda x: sev_order.get(x['severity'], 99))
        sev_icon = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🟢', 'info': 'ℹ️'}
        for iss in all_issues:
            ic = sev_icon.get(iss['severity'], '⚪')
            sid = iss.get('sql_id', 1)
            print(f"  {ic} [SQL#{sid}:L{iss['line']}] [{iss['rule']}] {iss['message']}")
        return
    sql = args.sql
    ln = SQLLinter(sql, config_path=config)
    issues = ln.lint()
    if fix_mode:
        fixed = ln.fix()
        print("修复后的 SQL:")
        print(fixed)
        print()
    print_section("🔍 Lint 规范检查")
    if not issues:
        print("  ✅ 未发现规范问题")
        return
    sev_count = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0, 'info': 0}
    for iss in issues:
        sev_count[iss['severity']] = sev_count.get(iss['severity'], 0) + 1
    print(f"  共发现 {len(issues)} 个问题: 🔴严重{sev_count['critical']} | 🟠高{sev_count['high']} | 🟡中{sev_count['medium']} | 🟢低{sev_count['low']} | ℹ️信息{sev_count['info']}")
    print()
    sev_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'info': 4}
    issues.sort(key=lambda x: sev_order.get(x['severity'], 99))
    sev_icon = {'critical': '🔴', 'high': '🟠', 'medium': '🟡', 'low': '🟢', 'info': 'ℹ️'}
    for iss in issues:
        ic = sev_icon.get(iss['severity'], '⚪')
        print(f"  {ic} [L{iss['line']}] [{iss['rule']}] {iss['message']}")


def main():
    parser = argparse.ArgumentParser(
        prog='sqlopt',
        description='SQL 语句分析与优化建议 CLI 工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python sqlopt.py parse "SELECT * FROM users WHERE id=1"
  python sqlopt.py analyze "SELECT * FROM users WHERE name LIKE '%张%'" --db test.db
  python sqlopt.py score "SELECT * FROM users u JOIN orders o ON u.id=o.user_id" --json
  python sqlopt.py score --file samples.sql --top 10
  python sqlopt.py diff --original "SELECT * FROM u WHERE id IN (SELECT uid FROM o)" --optimized "SELECT * FROM u JOIN o ON u.id=o.uid"
  python sqlopt.py schema --db test.db --refresh
  python sqlopt.py format "select id,name from users where age>18" --style upper --comma leading
  python sqlopt.py lint --file samples.sql --fix --config .sqlopt.yaml
  python sqlopt.py initdb --db test.db
  python sqlopt.py batch file.sql --db test.db --output report.md
        """
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_parse = sub.add_parser('parse', help='解析SQL语句，树形展示结构')
    p_parse.add_argument('sql', help='SQL语句')
    p_parse.set_defaults(func=cmd_parse)

    p_analyze = sub.add_parser('analyze', help='综合分析SQL: 解析+索引建议+反模式检测+执行计划+重写')
    p_analyze.add_argument('sql', help='SQL语句')
    p_analyze.add_argument('--db', help='SQLite数据库文件路径 (用于EXPLAIN和统计信息)', default=None)
    p_analyze.set_defaults(func=cmd_analyze)

    p_init = sub.add_parser('initdb', help='创建预置的测试数据库 (users/orders/products 各1000行)')
    p_init.add_argument('--db', help='数据库文件路径', default='test.db')
    p_init.set_defaults(func=cmd_initdb)

    p_batch = sub.add_parser('batch', help='批量分析SQL文件或应用日志，生成Markdown报告')
    p_batch.add_argument('file', help='SQL文件或日志文件路径')
    p_batch.add_argument('--log', action='store_true', help='按日志模式提取SQL (正则匹配)')
    p_batch.add_argument('--db', help='SQLite数据库文件路径', default=None)
    p_batch.add_argument('--output', '-o', help='输出Markdown报告路径', default=None)
    p_batch.set_defaults(func=cmd_batch)

    p_samples = sub.add_parser('samples', help='显示10条示例SQL')
    p_samples.set_defaults(func=cmd_samples)

    p_score = sub.add_parser('score', help='SQL 复杂度评分 (0-100分): 表数/JOIN/子查询/WHERE/临时表排序')
    p_score.add_argument('sql', nargs='?', help='SQL语句 (与--file二选一)')
    p_score.add_argument('--file', '-f', help='批量评分SQL文件，输出TOP10最复杂查询')
    p_score.add_argument('--db', help='SQLite数据库 (用于检测临时表排序)')
    p_score.add_argument('--json', action='store_true', help='JSON格式输出')
    p_score.add_argument('--top', type=int, default=10, help='批量模式时输出前N个 (默认10)')
    p_score.set_defaults(func=cmd_score)

    p_diff = sub.add_parser('diff', help='对比两条SQL的复杂度评分差异，高亮改进维度')
    p_diff.add_argument('--original', '-o', required=True, help='原始SQL')
    p_diff.add_argument('--optimized', '-n', required=True, help='优化后SQL')
    p_diff.add_argument('--json', action='store_true', help='JSON格式输出')
    p_diff.set_defaults(func=cmd_diff)

    p_schema = sub.add_parser('schema', help='从SQLite提取表结构(列/类型/主键/外键/索引)并缓存')
    p_schema.add_argument('--db', required=True, help='SQLite数据库文件路径')
    p_schema.add_argument('--refresh', action='store_true', help='强制刷新Schema缓存')
    p_schema.add_argument('--json', action='store_true', help='JSON格式输出')
    p_schema.set_defaults(func=cmd_schema)

    p_format = sub.add_parser('format', help='格式化SQL: 关键字大写、子句换行、缩进对齐、逗号位置可配置')
    p_format.add_argument('sql', nargs='?', help='SQL语句 (与--file二选一)')
    p_format.add_argument('--file', '-f', help='格式化.sql文件')
    p_format.add_argument('--style', choices=['upper', 'lower', 'preserve'], default='upper', help='关键字大小写风格 (默认: upper)')
    p_format.add_argument('--comma', choices=['leading', 'trailing'], default='leading', help='逗号位置: 前置或后置 (默认: leading)')
    p_format.add_argument('--indent', type=int, default=4, help='缩进空格数 (默认: 4)')
    p_format.add_argument('--fix', action='store_true', help='直接修改文件 (仅--file模式)')
    p_format.set_defaults(func=cmd_format)

    p_lint = sub.add_parser('lint', help='SQL规范检查: 蛇形命名/保留字/JOIN ON/别名/行尾空格等')
    p_lint.add_argument('sql', nargs='?', help='SQL语句 (与--file二选一)')
    p_lint.add_argument('--file', '-f', help='检查.sql文件')
    p_lint.add_argument('--config', '-c', help='.sqlopt.yaml 配置文件路径')
    p_lint.add_argument('--fix', action='store_true', help='自动修复可修复问题')
    p_lint.set_defaults(func=cmd_lint)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
