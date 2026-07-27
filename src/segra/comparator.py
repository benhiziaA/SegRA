# Copyright 2026 SegRA contributors
# SPDX-License-Identifier: Apache-2.0
import json
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


REALM_FULL_LOAD_LIMIT_BYTES = 100 * 1024 * 1024
REALM_STREAM_IDLE_LINE_LIMIT = 300_000
REALM_STREAM_MIN_LINES = 10_000

DOMAIN_TYPES = {"internal", "external"}
FUNCTION_TYPES = {
    "business",
    "environment",
    "hub",
    "infrastructure",
    "security",
    "splitInfrastructure",
}
# Serialized GoZ realm support.
REF_RE = re.compile(r"^<ref:([^:>]+):(.+)>$")
KEY_LINE_RE = re.compile(r'^\s*"((?:[^"\\]|\\.)*)":\s*(.*?)(?:,)?\s*$')

# Fact parsing
def parse_fact_args(fact: str) -> Optional[Tuple[str, List[str]]]:
    fact = fact.strip()
    if fact.endswith("."):
        fact = fact[:-1]

    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$", fact)
    if not m:
        return None

    args: List[str] = []
    current: List[str] = []
    depth = 0
    for ch in m.group(2):
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        args.append("".join(current).strip())

    return m.group(1), args


def fact_args(fact: str, predicate: str, arity: int) -> Optional[List[str]]:
    parsed = parse_fact_args(fact)
    if not parsed:
        return None
    pred, args = parsed
    if pred == predicate and len(args) == arity:
        return args
    return None


def parse_ref(value: Any) -> Optional[Tuple[str, str, str]]:
    if not isinstance(value, str):
        return None
    m = REF_RE.match(value)
    if not m:
        return None
    return "ref", m.group(1), m.group(2)


def ref_kind(value: Any) -> Optional[str]:
    if isinstance(value, tuple) and len(value) == 3 and value[0] == "ref":
        return value[1]
    if isinstance(value, dict):
        kind = value.get("__kind")
        return str(kind) if kind else None
    return None


def ref_name(value: Any) -> Optional[str]:
    if isinstance(value, tuple) and len(value) == 3 and value[0] == "ref":
        return value[2]
    if isinstance(value, dict):
        name = value.get("name")
        return str(name) if name else None
    if isinstance(value, str) and not value.startswith("<ref:"):
        return value
    return None


def ref_from_json_scalar(value: Any) -> Any:
    ref = parse_ref(value)
    return ref if ref else value


def parse_json_line_value(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.endswith(","):
        cleaned = cleaned[:-1].rstrip()
    value = json.loads(cleaned)
    return ref_from_json_scalar(value)


def is_named_realm_object(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("fields", {}).get("name"))

# Identify the realm object represented by a parsed context.
def guess_realm_object_kind(ctx: Dict[str, Any]) -> Optional[str]:
    fields = ctx.get("fields", {})
    seen_keys = ctx.get("seen_keys", set())
    name = fields.get("name")
    obj_type = fields.get("type")
    if not name:
        return None

    if "end1" in seen_keys and "end2" in seen_keys:
        return "Channel"

    if obj_type in DOMAIN_TYPES and (
        "zones" in seen_keys or "domains" in seen_keys or str(name).endswith("Domain")
    ):
        return "Domain"

    if str(name).startswith("zone") and ("distances" in seen_keys or "functions" in seen_keys):
        return "Zone"

    if obj_type in FUNCTION_TYPES:
        return "NetworkFunction"

    return None


class RealmFactCollector:
    def __init__(self) -> None:
        self.domains: Dict[str, str] = {}
        self.zones: Set[str] = set()
        self.zone_domains: Dict[str, str] = {}
        self.function_types: Dict[str, str] = {}
        self.function_zones: Dict[str, Set[str]] = {}
        self.function_domains: Dict[str, Set[str]] = {}
        self.requirements: Dict[str, Dict[str, Set[int]]] = {}
        self.channels: Set[Tuple[str, str]] = set()

    def marker(self) -> Tuple[int, int, int, int, int, int, int]:
        return (
            len(self.domains),
            len(self.zones),
            len(self.zone_domains),
            len(self.function_types),
            sum(len(v) for v in self.function_zones.values()),
            sum(len(v) for v in self.function_domains.values()),
            len(self.channels),
        )

    def has_architecture(self) -> bool:
        return bool(self.domains and self.zones and self.function_types and self.channels)

    def add_function_zone(self, function_name: str, zone_name: str) -> None:
        self.zones.add(zone_name)
        self.function_zones.setdefault(function_name, set()).add(zone_name)

    def add_function_domain(self, function_name: str, domain_name: str) -> None:
        self.function_domains.setdefault(function_name, set()).add(domain_name)

    def add_zone_domain(self, zone_name: str, domain_name: str) -> None:
        self.zones.add(zone_name)
        self.zone_domains.setdefault(zone_name, domain_name)

    def add_requirements(self, function_name: str, reqs: Dict[str, Any]) -> None:
        for kind, raw_values in reqs.items():
            if not isinstance(raw_values, list):
                continue
            values: Set[int] = set()
            for value in raw_values:
                if isinstance(value, int):
                    values.add(value)
                elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
                    values.add(int(value))
            if values:
                self.requirements.setdefault(function_name, {}).setdefault(kind, set()).update(values)

    def record_context(self, ctx: Dict[str, Any]) -> Dict[str, Optional[str]]:
        fields = ctx.get("fields", {})
        children = ctx.get("children", {})
        arrays = ctx.get("arrays", {})
        name = fields.get("name")
        obj_type = fields.get("type")
        kind = guess_realm_object_kind(ctx)

        if kind == "Channel":
            end1 = ref_name(children.get("end1", fields.get("end1")))
            end2 = ref_name(children.get("end2", fields.get("end2")))
            if end1 and end2:
                self.channels.add((end1, end2))

        elif kind == "Domain":
            self.domains[str(name)] = str(obj_type)
            for item in arrays.get("zones", []):
                zone_name = ref_name(item)
                if zone_name:
                    self.add_zone_domain(zone_name, str(name))
            for item in arrays.get("functions", []):
                function_name = ref_name(item)
                if function_name:
                    self.add_function_domain(function_name, str(name))

        elif kind == "Zone":
            self.zones.add(str(name))
            parent = children.get("parent", fields.get("parent"))
            if ref_kind(parent) == "Domain":
                domain_name = ref_name(parent)
                if domain_name:
                    self.add_zone_domain(str(name), domain_name)
            for item in arrays.get("functions", []):
                function_name = ref_name(item)
                if function_name:
                    self.add_function_zone(function_name, str(name))

        elif kind == "NetworkFunction":
            self.function_types[str(name)] = str(obj_type)
            parent = children.get("parent", fields.get("parent"))
            if ref_kind(parent) == "Zone":
                zone_name = ref_name(parent)
                if zone_name:
                    self.add_function_zone(str(name), zone_name)
            elif ref_kind(parent) == "Domain":
                domain_name = ref_name(parent)
                if domain_name:
                    self.add_function_domain(str(name), domain_name)
            reqs = children.get("requirements")
            if isinstance(reqs, dict):
                self.add_requirements(str(name), reqs)

        return {"__kind": kind, "name": str(name) if name else None, "type": str(obj_type) if obj_type else None}

    def facts(self) -> List[str]:
        facts: List[str] = []

        for domain_name, domain_type in sorted(self.domains.items()):
            facts.append(f"domain({domain_type}, {domain_name}).")

        for zone_name in sorted(self.zones):
            facts.append(f"zone(normal, {zone_name}).")

        for zone_name, domain_name in sorted(self.zone_domains.items()):
            facts.append(f"inDomain({domain_name}, {zone_name}).")

        for function_name, function_type in sorted(self.function_types.items()):
            facts.append(f"networkFunction({function_type}, {function_name}).")

        domain_members: Dict[str, Set[str]] = {k: set(v) for k, v in self.function_domains.items()}
        for function_name, zone_names in self.function_zones.items():
            for zone_name in zone_names:
                domain_name = self.zone_domains.get(zone_name)
                if domain_name:
                    domain_members.setdefault(function_name, set()).add(domain_name)

        for function_name, domain_names in sorted(domain_members.items()):
            for domain_name in sorted(domain_names):
                facts.append(f"inDomain({domain_name}, {function_name}).")

        for function_name, zone_names in sorted(self.function_zones.items()):
            for zone_name in sorted(zone_names):
                facts.append(f"inZone({zone_name}, {function_name}).")

        for function_name, reqs in sorted(self.requirements.items()):
            for kind, values in sorted(reqs.items()):
                value_text = ",".join(str(v) for v in sorted(values))
                facts.append(f"functionRequirements({kind},{function_name},({value_text})).")

        for end1, end2 in sorted(self.channels):
            facts.append(f"channel({end1}, {end2}).")

        return dedupe_facts(facts)

# Streaming parser for large serialized GoZ realm files.
def make_stream_object_context(attached_key: Optional[str] = None) -> Dict[str, Any]:
    return {
        "kind": "object",
        "attached_key": attached_key,
        "fields": {},
        "children": {},
        "arrays": {},
        "seen_keys": set(),
    }


def make_stream_array_context(key: Optional[str]) -> Dict[str, Any]:
    return {"kind": "array", "key": key, "values": []}


def attach_stream_value(stack: List[Dict[str, Any]], key: Optional[str], value: Any) -> None:
    if not stack:
        return
    parent = stack[-1]
    if parent.get("kind") == "array":
        parent["values"].append(value)
    elif parent.get("kind") == "object" and key:
        if isinstance(value, dict) and value.get("__kind") == "Requirements":
            parent["children"][key] = value.get("values", {})
        else:
            parent["children"][key] = value


def close_stream_array(stack: List[Dict[str, Any]]) -> None:
    array_ctx = stack.pop()
    key = array_ctx.get("key")
    values = array_ctx.get("values", [])
    if not stack:
        return
    parent = stack[-1]
    if parent.get("kind") == "array":
        parent["values"].append(values)
    elif parent.get("kind") == "object" and key:
        parent["arrays"][key] = values
        parent["fields"][key] = values


def close_stream_object(stack: List[Dict[str, Any]], collector: RealmFactCollector) -> None:
    object_ctx = stack.pop()
    attached_key = object_ctx.get("attached_key")

    if attached_key == "requirements" and not is_named_realm_object(object_ctx):
        summary: Dict[str, Any] = {"__kind": "Requirements", "values": object_ctx.get("arrays", {})}
    else:
        summary = collector.record_context(object_ctx)

    attach_stream_value(stack, attached_key, summary)


def stream_realm_asp_facts(path: Path) -> List[str]:
    collector = RealmFactCollector()
    stack: List[Dict[str, Any]] = []
    last_marker = collector.marker()
    last_new_line = 0

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("{") and stripped.rstrip(",") == "{":
                stack.append(make_stream_object_context())
            elif stripped.startswith("[") and stripped.rstrip(",") == "[":
                stack.append(make_stream_array_context(None))
            elif stripped.startswith("}"):
                if stack and stack[-1].get("kind") == "object":
                    close_stream_object(stack, collector)
            elif stripped.startswith("]"):
                if stack and stack[-1].get("kind") == "array":
                    close_stream_array(stack)
            else:
                m = KEY_LINE_RE.match(line)
                if m and stack:
                    key = json.loads(f'"{m.group(1)}"')
                    rest = m.group(2).strip()
                    current = stack[-1]
                    if current.get("kind") != "object":
                        continue
                    current["seen_keys"].add(key)

                    rest_no_comma = rest[:-1].rstrip() if rest.endswith(",") else rest
                    if rest_no_comma == "{}":
                        if key == "requirements":
                            current["children"][key] = {}
                        else:
                            current["fields"][key] = {}
                    elif rest_no_comma == "[]":
                        current["arrays"][key] = []
                        current["fields"][key] = []
                    elif rest_no_comma == "{":
                        stack.append(make_stream_object_context(attached_key=key))
                    elif rest_no_comma == "[":
                        stack.append(make_stream_array_context(key))
                    else:
                        current["fields"][key] = parse_json_line_value(rest)
                elif stack and stack[-1].get("kind") == "array" and stripped not in {",", "{", "["}:
                    stack[-1]["values"].append(parse_json_line_value(stripped))

            marker = collector.marker()
            if marker != last_marker:
                last_marker = marker
                last_new_line = line_no
            elif (
                collector.has_architecture()
                and line_no > REALM_STREAM_MIN_LINES
                and line_no - last_new_line > REALM_STREAM_IDLE_LINE_LIMIT
            ):
                break

    return collector.facts()


def summarize_realm_dict(value: Dict[str, Any]) -> Dict[str, Optional[str]]:
    fields: Dict[str, Any] = {}
    seen_keys = set(value)
    for key, child in value.items():
        if not isinstance(child, (dict, list)):
            fields[key] = ref_from_json_scalar(child)
    ctx = {"fields": fields, "children": {}, "arrays": {}, "seen_keys": seen_keys}
    return {"__kind": guess_realm_object_kind(ctx), "name": fields.get("name"), "type": fields.get("type")}


def extract_realm_asp_facts(data: Any) -> List[str]:
    collector = RealmFactCollector()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        for child in value.values():
            visit(child)

        fields: Dict[str, Any] = {}
        children: Dict[str, Any] = {}
        arrays: Dict[str, Any] = {}
        for key, child in value.items():
            if isinstance(child, dict):
                if key == "requirements":
                    children[key] = {
                        req_key: req_values
                        for req_key, req_values in child.items()
                        if isinstance(req_values, list)
                    }
                else:
                    children[key] = summarize_realm_dict(child)
            elif isinstance(child, list):
                array_values: List[Any] = []
                for item in child:
                    if isinstance(item, dict):
                        array_values.append(summarize_realm_dict(item))
                    else:
                        array_values.append(ref_from_json_scalar(item))
                arrays[key] = array_values
                fields[key] = array_values
            else:
                fields[key] = ref_from_json_scalar(child)

        collector.record_context(
            {"fields": fields, "children": children, "arrays": arrays, "seen_keys": set(value)}
        )

    visit(data)
    return collector.facts()


def find_asp_facts(value: Any) -> Optional[List[str]]:
    if isinstance(value, dict):
        facts = value.get("asp_facts")
        if isinstance(facts, list) and all(isinstance(fact, str) for fact in facts):
            return facts
        for child in value.values():
            found = find_asp_facts(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_asp_facts(child)
            if found is not None:
                return found
    return None

# Load an architecture from asp_facts or a serialized GoZ realm.
def load_architecture(path: str) -> Dict[str, Any]:
    in_path = Path(path).expanduser().resolve()
    if in_path.stat().st_size <= REALM_FULL_LOAD_LIMIT_BYTES:
        with in_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        facts = find_asp_facts(data)
        if facts is not None:
            if isinstance(data, dict) and data.get("asp_facts") is facts:
                return data
            return {
                "type": "RealmLike",
                "source_format": "nested_asp_facts",
                "source_json": str(in_path),
                "asp_facts": facts,
            }

        realm_facts = extract_realm_asp_facts(data)
        if realm_facts:
            return {
                "type": "RealmLike",
                "source_format": "serialized_realm",
                "source_json": str(in_path),
                "asp_facts": realm_facts,
            }
        raise ValueError(f"{path} must contain an asp_facts list or a serialized GoZ realm")

    realm_facts = stream_realm_asp_facts(in_path)
    if not realm_facts:
        raise ValueError(f"{path} is too large to load and no serialized GoZ realm facts were detected")
    return {
        "type": "RealmLike",
        "source_format": "streamed_serialized_realm",
        "source_json": str(in_path),
        "asp_facts": realm_facts,
    }



def write_json(path: str, data: Dict[str, Any]) -> None:
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): normalize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_for_json(v) for v in value]
    if isinstance(value, set):
        return [normalize_for_json(v) for v in sorted(value, key=str)]
    if isinstance(value, tuple):
        return [normalize_for_json(v) for v in value]
    return value


def is_dns_dhcp_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered.startswith("dns") or lowered.startswith("dhcp")


def is_zone_switch(name: str) -> bool:
    return re.match(r"^switch\(zone[^)]+\)$", name.strip()) is not None


def is_switch(name: str) -> bool:
    return name.strip().startswith("switch(")


def is_control(name: str) -> bool:
    return name.startswith("actuator(") or name.startswith("split(") or is_switch(name)


def is_payload_member(name: str, function_type: Optional[str]) -> bool:
    if is_control(name):
        return False
    if is_dns_dhcp_name(name):
        return False
    if function_type in {"hub", "security", "splitInfrastructure"}:
        return False
    return True


def split_targets(split_name: str) -> Set[str]:
    parsed = parse_fact_args(split_name)
    if not parsed or parsed[0] != "split" or len(parsed[1]) != 2:
        return set()

    target = parsed[1][1].strip()
    if target.startswith("(") and target.endswith(")"):
        inner = target[1:-1]
        return {part.strip() for part in inner.split(",") if part.strip()}
    return {target}


def split_source(split_name: str) -> Optional[str]:
    parsed = parse_fact_args(split_name)
    if not parsed or parsed[0] != "split" or len(parsed[1]) != 2:
        return None
    return parsed[1][0].strip()


def actuator_kind_and_target(name: str) -> Tuple[Optional[str], Optional[str]]:
    parsed = parse_fact_args(name)
    if not parsed or parsed[0] != "actuator" or len(parsed[1]) != 2:
        return None, None
    return parsed[1][0].strip(), parsed[1][1].strip()

# Parsed architecture model
class Zone:
    def __init__(self, name: str) -> None:
        self.name = name
        self.members: List[str] = []
        self.payloads: Set[str] = set()
        self.controls: Set[str] = set()
        self.domain: Optional[str] = None


class ParsedArchitecture:
    def __init__(self, facts: List[str]) -> None:
        self.facts = facts
        self.zone_names: Set[str] = set()
        self.zones: Dict[str, Zone] = {}
        self.member_zones: Dict[str, Set[str]] = {}
        self.domain_types: Dict[str, str] = {}
        self.domain_members: Dict[str, Set[str]] = {}
        self.function_types: Dict[str, str] = {}
        self.function_requirements: Dict[str, Dict[str, List[int]]] = {}
        self.zone_graph: Dict[str, Set[str]] = {}
        self.boundary_edges: List[Tuple[str, str]] = []
        self.external_members: Set[str] = set()
        self._parse()

    def zone(self, name: str) -> Zone:
        self.zone_names.add(name)
        self.zone_graph.setdefault(name, set())
        if name not in self.zones:
            self.zones[name] = Zone(name)
        return self.zones[name]

    def _parse(self) -> None:
        for fact in self.facts:
            args = fact_args(fact, "domain", 2)
            if args:
                self.domain_types[args[1]] = args[0]
                self.domain_members.setdefault(args[1], set())
                continue

            args = fact_args(fact, "networkFunction", 2)
            if args:
                self.function_types[args[1]] = args[0]
                continue

            args = fact_args(fact, "functionRequirements", 3)
            if args:
                req_kind, fn_name, tuple_text = args
                self.function_requirements.setdefault(fn_name, {})[req_kind] = parse_req_values(tuple_text)
                continue

            args = fact_args(fact, "inDomain", 2)
            if args:
                domain, member = args
                self.domain_members.setdefault(domain, set()).add(member)
                if member.startswith("zone"):
                    self.zone(member).domain = domain
                continue

            args = fact_args(fact, "inZone", 2)
            if args:
                zone_name, member = args
                zone = self.zone(zone_name)
                zone.members.append(member)
                self.member_zones.setdefault(member, set()).add(zone_name)
                continue

            args = fact_args(fact, "zone", 2)
            if args:
                self.zone(args[1])
                continue

            args = fact_args(fact, "channel", 2)
            if args:
                z1 = zone_from_switch(args[0])
                z2 = zone_from_switch(args[1])
                if z1 and z2:
                    self.zone_graph.setdefault(z1, set()).add(z2)
                    self.zone_graph.setdefault(z2, set()).add(z1)
                    continue

                for left, right in ((args[0], args[1]), (args[1], args[0])):
                    domain = domain_from_switch(left)
                    zone = zone_from_switch(right)
                    if domain and zone:
                        self.boundary_edges.append((domain, zone))
                        self.zone(zone)
                        break

        for domain, dtype in self.domain_types.items():
            if dtype == "external":
                self.external_members.update(self.domain_members.get(domain, set()))

        for zone in self.zones.values():
            for member in zone.members:
                ftype = self.function_types.get(member)
                if is_payload_member(member, ftype):
                    zone.payloads.add(member)
                elif member.startswith("actuator(") or member.startswith("split(") or is_zone_switch(member):
                    zone.controls.add(member)


def parse_req_values(tuple_text: str) -> List[int]:
    text = tuple_text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    if not text:
        return []
    return [int(part.strip()) for part in text.split(",") if part.strip()]


def zone_from_switch(value: str) -> Optional[str]:
    m = re.match(r"^switch\((zone[^)]+)\)$", value.strip())
    return m.group(1) if m else None


def domain_from_switch(value: str) -> Optional[str]:
    m = re.match(r"^switch\(([^)]+)\)$", value.strip())
    if not m:
        return None
    name = m.group(1)
    return None if name.startswith("zone") else name


# Distance used to map optimal zones to real zones.
def jaccard_distance(a: Set[str], b: Set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return 1.0 - (len(a & b) / len(union))

# Build the real-zone image for an optimal zone.
def refinement_image(
    opt_zone: Zone,
    real: ParsedArchitecture,
    remaining_zone_names: Optional[Set[str]] = None,
) -> List[str]:
    available = remaining_zone_names if remaining_zone_names is not None else set(real.zones)
    grouped = [
        name
        for name in sorted(available)
        if real.zones[name].payloads
        and real.zones[name].payloads & opt_zone.payloads
        and real.zones[name].payloads.issubset(opt_zone.payloads)
    ]
    if not grouped:
        return []

    union: Set[str] = set()
    for name in grouped:
        union |= real.zones[name].payloads

    if union == opt_zone.payloads:
        return sorted(grouped)
    return []


def match_optimal_to_real(optimal: ParsedArchitecture, real: ParsedArchitecture) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {
        opt_zone.name: [] for opt_zone in sorted(optimal.zones.values(), key=lambda z: z.name)
    }
    remaining: Set[str] = set(real.zones)

    for opt_zone in sorted(optimal.zones.values(), key=lambda z: z.name):
        image = refinement_image(opt_zone, real, remaining)
        if image:
            mapping[opt_zone.name] = image
            remaining -= set(image)
            continue

        if remaining:
            best = min(
                sorted(remaining),
                key=lambda real_name: jaccard_distance(opt_zone.payloads, real.zones[real_name].payloads),
            )
            mapping[opt_zone.name] = [best]
            remaining.remove(best)

    for real_name in sorted(remaining):
        best_opt = min(
            sorted(optimal.zones),
            key=lambda opt_name: jaccard_distance(
                optimal.zones[opt_name].payloads,
                real.zones[real_name].payloads,
            ),
        )
        mapping[best_opt].append(real_name)

    return mapping

# Assets inside the real-zone image of an optimal zone.
def image_payloads(real: ParsedArchitecture, real_image: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for real_zone in real_image:
        zone = real.zones.get(real_zone)
        if zone:
            out |= zone.payloads
    return out


def add_misplaced_payloads(results: Dict[str, Any]) -> None:
    missing_locations: Dict[str, Set[str]] = {}
    wrong_locations: Dict[str, Set[str]] = {}

    for opt_zone, res in results.items():
        if opt_zone.startswith("_"):
            continue
        for payload in res.get("miss", []):
            missing_locations.setdefault(payload, set()).add(opt_zone)
        for payload in res.get("wrong", []):
            wrong_locations.setdefault(payload, set()).add(opt_zone)

    misplaced: Set[str] = set()
    for payload, missing_zones in missing_locations.items():
        for wrong_zone in wrong_locations.get(payload, set()):
            if any(missing_zone != wrong_zone for missing_zone in missing_zones):
                misplaced.add(payload)
                break

    for opt_zone, res in results.items():
        if opt_zone.startswith("_"):
            continue
        local = (set(res.get("miss", [])) | set(res.get("wrong", []))) & misplaced
        res["misplaced"] = sorted(local)

    results["_misplaced_payloads"] = sorted(misplaced)

# Phase 1: zone mapping and asset placement.
def step1_compare(optimal: ParsedArchitecture, real: ParsedArchitecture) -> Dict[str, Any]:
    mapping = match_optimal_to_real(optimal, real)
    results: Dict[str, Any] = {}

    for opt_name, real_image in mapping.items():
        opt_zone = optimal.zones[opt_name]
        real_image = sorted(real_image)
        if not real_image:
            results[opt_name] = {
                "type": "miss",
                "matched_zone": None,
                "real_image": [],
                "good": [],
                "miss": sorted(opt_zone.payloads),
                "wrong": [],
            }
            continue

        real_payloads = image_payloads(real, real_image)
        good = opt_zone.payloads & real_payloads
        miss = opt_zone.payloads - real_payloads
        wrong = real_payloads - opt_zone.payloads

        res: Dict[str, Any] = {
            "matched_zone": real_image[0] if len(real_image) == 1 else None,
            "real_image": real_image,
            "jaccard_distance": min(
                jaccard_distance(opt_zone.payloads, real.zones[real_name].payloads)
                for real_name in real_image
            ),
            "good": sorted(good),
            "miss": sorted(miss),
            "wrong": sorted(wrong),
        }
        if not miss and not wrong:
            if len(real_image) == 1:
                res["type"] = "exact"
                res["matched_zone"] = real_image[0]
            else:
                res["type"] = "valid_refinement"
                res["refinement_zones"] = real_image
        else:
            res["type"] = "violation"
            if miss and wrong:
                res["reason"] = "missing and wrong payloads"
            elif miss:
                res["reason"] = "missing payloads"
            else:
                res["reason"] = "wrong payloads present"
        results[opt_name] = res

    add_misplaced_payloads(results)

    matched = {
        real_zone
        for res in results.values()
        if isinstance(res, dict)
        for real_zone in res.get("real_image", [])
    }
    results["_unmatched_real_zones"] = sorted(name for name in real.zones if name not in matched)
    return results
# Roots for defense-in-depth distance are the nearest zones reachable from external domains.
def infer_roots(parsed: ParsedArchitecture) -> List[str]:
    external_domains = {
        name for name, domain_type in parsed.domain_types.items() if domain_type == "external"
    }
    if not external_domains:
        return sorted({zone for _, zone in parsed.boundary_edges})

    graph: Dict[str, Set[str]] = {zone: set(neighbors) for zone, neighbors in parsed.zone_graph.items()}

    def add_edge(left: str, right: str) -> None:
        if left and right and left != right:
            graph.setdefault(left, set()).add(right)
            graph.setdefault(right, set()).add(left)

    def topology_node(value: str) -> str:
        zone = zone_from_switch(value)
        if zone:
            return zone
        domain = domain_from_switch(value)
        if domain and domain in parsed.domain_types:
            return domain
        return value.strip()

    for fact in parsed.facts:
        args = fact_args(fact, "adjacent", 2)
        if args:
            add_edge(args[0], args[1])
            continue

        args = fact_args(fact, "inDomain", 2)
        if args and args[0] in external_domains:
            add_edge(args[0], args[1])
            continue

        args = fact_args(fact, "channel", 2)
        if args:
            add_edge(topology_node(args[0]), topology_node(args[1]))

    seen = set(external_domains)
    queue = deque(external_domains)
    while queue:
        next_queue = deque()
        roots: Set[str] = set()
        while queue:
            node = queue.popleft()
            for neighbor in graph.get(node, set()):
                if neighbor in parsed.zones:
                    roots.add(neighbor)
                elif neighbor not in seen:
                    seen.add(neighbor)
                    next_queue.append(neighbor)
        if roots:
            return sorted(roots)
        queue = next_queue

    return sorted({zone for _, zone in parsed.boundary_edges})

# Phase 2: defense-in-depth distance comparison.
def shortest_depths(graph: Dict[str, Set[str]], roots: Iterable[str]) -> Dict[str, Optional[int]]:
    depths: Dict[str, Optional[int]] = {node: None for node in graph}
    queue = deque()
    for root in roots:
        if root in depths:
            depths[root] = 0
            queue.append(root)

    while queue:
        current = queue.popleft()
        current_depth = depths[current]
        if current_depth is None:
            continue
        for neighbor in graph.get(current, set()):
            if depths.get(neighbor) is None:
                depths[neighbor] = current_depth + 1
                queue.append(neighbor)
    return depths


def classify_depth(delta: Optional[int]) -> str:
    if delta is None:
        return "no_mapping_available"
    if delta == 0:
        return "well_located"
    if delta < 0:
        return "overexposed"
    return "over_protected"


def step2_compare(
    optimal: ParsedArchitecture,
    real: ParsedArchitecture,
    step1: Dict[str, Any],
) -> Dict[str, Any]:
    opt_depths = shortest_depths(optimal.zone_graph, infer_roots(optimal))
    real_depths = shortest_depths(real.zone_graph, infer_roots(real))
    results: Dict[str, Any] = {}

    for opt_zone in sorted(optimal.zones):
        real_image = list(step1.get(opt_zone, {}).get("real_image", []))
        d_opt = opt_depths.get(opt_zone)
        d_real_by_zone: Dict[str, Optional[int]] = {}
        delta_by_zone: Dict[str, Optional[int]] = {}
        classification_by_zone: Dict[str, str] = {}

        for real_zone in real_image:
            d_real = real_depths.get(real_zone)
            delta = None if d_opt is None or d_real is None else d_real - d_opt
            d_real_by_zone[real_zone] = d_real
            delta_by_zone[real_zone] = delta
            classification_by_zone[real_zone] = classify_depth(delta)

        if not real_image:
            classification = "no_mapping_available"
        elif any(kind == "overexposed" for kind in classification_by_zone.values()):
            classification = "overexposed"
        elif any(kind == "no_mapping_available" for kind in classification_by_zone.values()):
            classification = "no_mapping_available"
        elif all(kind == "well_located" for kind in classification_by_zone.values()):
            classification = "well_located"
        else:
            classification = "over_protected"

        first_real_zone = real_image[0] if real_image else None
        results[opt_zone] = {
            "matched_real_zone": first_real_zone if len(real_image) == 1 else None,
            "real_image": real_image,
            "d_opt": d_opt,
            "d_real": d_real_by_zone.get(first_real_zone) if first_real_zone else None,
            "delta_d": delta_by_zone.get(first_real_zone) if first_real_zone else None,
            "d_real_by_zone": d_real_by_zone,
            "delta_d_by_zone": delta_by_zone,
            "classification_by_zone": classification_by_zone,
            "overexposed_zones": sorted(
                real_zone
                for real_zone, kind in classification_by_zone.items()
                if kind == "overexposed"
            ),
            "classification": classification,
        }
    return results


def split_target_text(split_name: str) -> str:
    targets = sorted(split_targets(split_name))
    if not targets:
        return ""
    if len(targets) == 1:
        return targets[0]
    return "(" + ",".join(targets) + ")"

# Phase 3: security-control comparison.
def semantic_control_token(control: str) -> Optional[str]:
    if control.startswith("split("):
        source = split_source(control)
        targets = split_target_text(control)
        if source and targets:
            return f"Split({source},{targets})"
        return control

    kind, target = actuator_kind_and_target(control)
    if kind == "pf":
        return "PF"
    if kind == "vpn":
        return "VPN"
    if kind == "alf" and target:
        return f"ALF({target})"
    if kind:
        return f"Actuator({kind},{target})" if target else f"Actuator({kind})"
    return None


def control_tokens_with_sources(zone: Zone) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for control in zone.controls:
        token = semantic_control_token(control)
        if token:
            out.setdefault(token, set()).add(control)
    return out


def controls_for_image(parsed: ParsedArchitecture, real_image: Iterable[str]) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for real_zone_name in real_image:
        zone = parsed.zones.get(real_zone_name)
        if not zone:
            continue
        for token, sources in control_tokens_with_sources(zone).items():
            out.setdefault(token, set()).update(sources)
    return out


def step3_compare(
    optimal: ParsedArchitecture,
    real: ParsedArchitecture,
    step1: Dict[str, Any],
) -> Dict[str, Any]:
    results: Dict[str, Any] = {}

    for opt_zone_name, opt_zone in sorted(optimal.zones.items()):
        real_image = list(step1.get(opt_zone_name, {}).get("real_image", []))
        optimal_controls = control_tokens_with_sources(opt_zone)
        real_controls = controls_for_image(real, real_image)
        missing = sorted(set(optimal_controls) - set(real_controls))

        results[opt_zone_name] = {
            "real_image": real_image,
            "optimal_controls": sorted(optimal_controls),
            "real_controls": sorted(real_controls),
            "missing_controls": missing,
            "compliant": not missing,
            "optimal_control_sources": {
                token: sorted(sources) for token, sources in sorted(optimal_controls.items())
            },
            "real_control_sources": {
                token: sorted(sources) for token, sources in sorted(real_controls.items())
            },
        }

    return results

# Remove duplicate facts while preserving order.
def dedupe_facts(facts: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for fact in facts:
        if fact not in seen:
            out.append(fact)
            seen.add(fact)
    return out



def compact(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (set, tuple, list)):
        items = list(value)
        return ", ".join(str(v) for v in items) if items else "-"
    if isinstance(value, dict):
        return json.dumps(normalize_for_json(value), ensure_ascii=False)
    return str(value)


# Report generation
def generate_pdf_report(output_pdf: str, validation: Dict[str, Any]) -> None:
    from xml.sax.saxutils import escape

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "PDF report generation requires reportlab. Install it with "
            "`python3 -m pip install reportlab`."
        ) from exc

    out = Path(output_pdf).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        rightMargin=1.35 * cm,
        leftMargin=1.35 * cm,
        topMargin=1.35 * cm,
        bottomMargin=1.35 * cm,
    )
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    small = ParagraphStyle("Small", parent=body, fontSize=7.4, leading=9)
    small_bold = ParagraphStyle("SmallBold", parent=small, fontName="Helvetica-Bold")
    subtitle = ParagraphStyle("Subtitle", parent=body, fontSize=9.5, leading=12)

    good = colors.HexColor("#D9EAD3")
    issue = colors.HexColor("#F4CCCC")
    header = colors.HexColor("#D9EAF7")
    story: List[Any] = []

    def para(text: Any, style: ParagraphStyle = small) -> Paragraph:
        return Paragraph(escape(compact(text)), style)

    def add_heading(text: str) -> None:
        story.append(Paragraph(escape(text), styles["Heading2"]))
        story.append(Spacer(1, 6))

    def add_table(
        rows: List[List[Any]],
        good_rows: Optional[Set[int]] = None,
        issue_rows: Optional[Set[int]] = None,
    ) -> None:
        if not rows:
            return
        good_rows = good_rows or set()
        issue_rows = issue_rows or set()
        table_data = [[para(cell, small_bold if r == 0 else small) for cell in row] for r, row in enumerate(rows)]
        table = Table(table_data, repeatRows=1)
        table_style = TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#9E9E9E")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
        for row_index in good_rows:
            table_style.add("BACKGROUND", (0, row_index), (-1, row_index), good)
        for row_index in issue_rows:
            table_style.add("BACKGROUND", (0, row_index), (-1, row_index), issue)
        table.setStyle(table_style)
        story.append(table)
        story.append(Spacer(1, 8))

    def step1_label(kind: Any) -> str:
        labels = {
            "exact": "Good",
            "valid_refinement": "Good refinement",
            "miss": "Miss",
            "violation": "Wrong / miss",
        }
        return labels.get(str(kind), compact(kind))

    def step2_label(kind: Any) -> str:
        labels = {
            "well_located": "Well located",
            "over_protected": "Overprotected",
            "overexposed": "Overexposed",
            "no_mapping_available": "No mapping available",
        }
        return labels.get(str(kind), compact(kind))

    def step2_value_list(res: Dict[str, Any], key: str) -> Any:
        values = []
        by_zone = res.get(key, {})
        if isinstance(by_zone, dict):
            for zone in res.get("real_image", []):
                if zone in by_zone:
                    values.append(by_zone[zone])
        if not values or any(value is None for value in values):
            return "-"
        return values[0] if len(values) == 1 else values

    def step1_status(res: Dict[str, Any]) -> str:
        if res.get("type") == "exact":
            return "Good"
        if res.get("type") == "valid_refinement":
            return "Good refinement"
        if res.get("miss") and res.get("wrong"):
            return "Wrong and miss"
        if res.get("miss"):
            return "Miss"
        if res.get("wrong"):
            return "Wrong"
        return step1_label(res.get("type"))

    def add_color_legend() -> None:
        add_table(
            [
                ["Color", "Meaning"],
                ["White", "Exact match, well located, or required controls available"],
                ["Green", "Valid refinement or overprotected placement"],
                ["Red", "Violation: wrong, missing, overexposed, no mapping available, missed controls, or unneeded controls"],
            ],
            good_rows={2},
            issue_rows={3},
        )

    def misplaced_summary_rows(step1: Dict[str, Any]) -> List[List[Any]]:
        expected: Dict[str, Set[str]] = {}
        found_in_images: Dict[str, Set[str]] = {}
        for opt_zone, res in step1.items():
            if opt_zone.startswith("_"):
                continue
            for payload in res.get("miss", []):
                expected.setdefault(payload, set()).add(opt_zone)
            for payload in res.get("wrong", []):
                found_in_images.setdefault(payload, set()).update(res.get("real_image", []))

        rows = [["Asset", "Expected zone", "Found in real zone image"]]
        for payload in step1.get("_misplaced_payloads", []):
            rows.append(
                [
                    payload,
                    sorted(expected.get(payload, set())),
                    sorted(found_in_images.get(payload, set())),
                ]
            )
        if len(rows) == 1:
            rows.append(["No misplaced assets", "-", "-"])
        return rows

    step1_before = validation["step1_before"]
    step2_before = validation["step2_before"]
    step3_before = validation.get("step3_before", {})

    story.append(Paragraph("Architecture Evaluation Report", styles["Title"]))
    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            escape(
                "Phase-by-phase evaluation of the real architecture against the optimal architecture. "
                f"Generated {datetime.now().isoformat(timespec='seconds')}."
            ),
            subtitle,
        )
    )
    story.append(Spacer(1, 8))
    add_color_legend()

    add_heading("Phase 1 - Zone Classification")
    rows = [["Optimal zone", "Classification", "Real image", "Good", "Wrong", "Miss"]]
    good_rows: Set[int] = set()
    issue_rows: Set[int] = set()
    for opt_zone, res in step1_before.items():
        if opt_zone.startswith("_"):
            continue
        row_index = len(rows)
        rows.append(
            [
                opt_zone,
                step1_status(res),
                res.get("real_image", []),
                res.get("good", []),
                res.get("wrong", []),
                res.get("miss", []),
            ]
        )
        if res.get("type") == "valid_refinement":
            good_rows.add(row_index)
        elif res.get("type") != "exact":
            issue_rows.add(row_index)
    add_table(rows, good_rows=good_rows, issue_rows=issue_rows)

    add_heading("Misplaced Assets Summary")
    misplaced_rows = misplaced_summary_rows(step1_before)
    misplaced_issue_rows = set(range(1, len(misplaced_rows))) if step1_before.get("_misplaced_payloads") else set()
    add_table(misplaced_rows, issue_rows=misplaced_issue_rows)

    add_heading("Phase 2 - Depth And Exposure")
    phase2_counts = {
        "Well located": 0,
        "Overprotected": 0,
        "Overexposed": 0,
        "No mapping available": 0,
    }
    for res in step2_before.values():
        label = step2_label(res.get("classification"))
        phase2_counts[label] = phase2_counts.get(label, 0) + 1
    add_table(
        [
            ["Well located", "Overprotected", "Overexposed", "No mapping available"],
            [
                phase2_counts.get("Well located", 0),
                phase2_counts.get("Overprotected", 0),
                phase2_counts.get("Overexposed", 0),
                phase2_counts.get("No mapping available", 0),
            ],
        ]
    )
    rows = [["Optimal zone", "Real image", "Optimal depth", "Real depth", "Delta", "Classification"]]
    good_rows = set()
    issue_rows = set()
    for opt_zone, res in step2_before.items():
        row_index = len(rows)
        classification = res.get("classification")
        rows.append(
            [
                opt_zone,
                res.get("real_image", []),
                res.get("d_opt"),
                step2_value_list(res, "d_real_by_zone"),
                step2_value_list(res, "delta_d_by_zone"),
                step2_label(classification),
            ]
        )
        if classification in {"overexposed", "no_mapping_available"}:
            issue_rows.add(row_index)
        elif classification == "over_protected":
            good_rows.add(row_index)
    add_table(rows, good_rows=good_rows, issue_rows=issue_rows)

    story.append(PageBreak())
    add_heading("Phase 3 - Security Controls")
    phase3_counts = {
        "Required controls available": 0,
        "Missed controls": 0,
        "Unneeded controls": 0,
    }
    for res in step3_before.values():
        required = set(res.get("optimal_controls", []))
        actual = set(res.get("real_controls", []))
        if required - actual:
            phase3_counts["Missed controls"] += 1
        elif actual - required:
            phase3_counts["Unneeded controls"] += 1
        else:
            phase3_counts["Required controls available"] += 1
    add_table(
        [
            ["Required controls available", "Missed controls", "Unneeded controls"],
            [
                phase3_counts["Required controls available"],
                phase3_counts["Missed controls"],
                phase3_counts["Unneeded controls"],
            ],
        ]
    )
    rows = [
        [
            "Optimal zone",
            "Real image",
            "Classification",
            "Required controls available",
            "Missed controls",
            "Unneeded controls",
        ]
    ]
    good_rows = set()
    issue_rows = set()
    for opt_zone, res in step3_before.items():
        row_index = len(rows)
        required = set(res.get("optimal_controls", []))
        actual = set(res.get("real_controls", []))
        available = sorted(required & actual)
        missing = sorted(required - actual)
        not_needed = sorted(actual - required)
        if missing:
            classification = "Missed controls"
            issue_rows.add(row_index)
        elif not_needed:
            classification = "Unneeded controls"
            issue_rows.add(row_index)
        else:
            classification = "Required controls available"
        rows.append(
            [
                opt_zone,
                res.get("real_image", []),
                classification,
                available,
                missing,
                not_needed,
            ]
        )
    add_table(rows, good_rows=good_rows, issue_rows=issue_rows)

    doc.build(story)

def evaluate_architecture(
    optimal_json: str,
    real_json: str,
    report_json: Optional[str] = None,
    report_pdf: Optional[str] = None,
) -> Dict[str, Any]:
    optimal_data = load_architecture(optimal_json)
    real_data = load_architecture(real_json)

    optimal = ParsedArchitecture(optimal_data["asp_facts"])
    real = ParsedArchitecture(real_data["asp_facts"])

    step1 = step1_compare(optimal, real)
    step2 = step2_compare(optimal, real, step1)
    step3 = step3_compare(optimal, real, step1)

    validation = {
        "stage": "real-architecture-evaluation",
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "step1_before": step1,
        "step2_before": step2,
        "step3_before": step3,
    }
    if report_json:
        write_json(report_json, validation)
    if report_pdf:
        generate_pdf_report(output_pdf=report_pdf, validation=validation)

    return validation
