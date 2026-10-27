"""Confirmed customer tickets with the supplied DOCX field schema."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
import uuid
from zipfile import ZipFile

from .account_tools import ToolError


def render_ticket(template, destination, values):
    """Keep the template's OOXML styles/media; expand its simple field/loop tags."""
    from lxml import etree
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    def text(node): return "".join(node.xpath(".//w:t/text()", namespaces=ns))
    def replace(node, mapping, remove=()):
        paragraphs = ([node] if node.tag.endswith("}p") else node.xpath(".//w:p", namespaces=ns) if remove else [])
        for paragraph in paragraphs:
            runs = paragraph.xpath(".//w:t", namespaces=ns)
            if not runs: continue
            value = "".join(run.text or "" for run in runs)
            changes = [(m.start(), m.end(), str(mapping[m[1]])) for m in re.finditer(r"\{(\w+)\}", value) if m[1] in mapping]
            for marker in remove:
                changes.extend((m.start(), m.end(), "") for m in re.finditer(re.escape(marker), value))
            for start, end, replacement in sorted(changes, reverse=True):
                position, affected = 0, []
                for run in runs:
                    current = run.text or ""
                    if position < end and position + len(current) > start:
                        affected.append((run, position, current))
                    position += len(current)
                if not affected: continue
                first, offset, original = affected[0]
                last, last_offset, last_original = affected[-1]
                if first is last:
                    first.text = original[:start - offset] + replacement + original[end - offset:]
                else:
                    first.text = original[:start - offset] + replacement
                    for run, _, _ in affected[1:-1]: run.text = ""
                    last.text = last_original[end - last_offset:]
    def expand(parent, mapping):
        children = list(parent)
        i = 0
        while i < len(children):
            node = children[i]
            match = re.match(r"\{([#^])(\w+)\}", text(node))
            if not match:
                expand(node, mapping)
                i += 1
                continue
            mode, key = match.groups()
            closing = "{/" + key + "}"
            end = i
            while end < len(children) and closing not in text(children[end]): end += 1
            if end == len(children):
                expand(node, mapping)
                i += 1
                continue
            group = children[i:end + 1]
            value = mapping.get(key)
            contexts = ([mapping] if not value else []) if mode == "^" else [dict(mapping, **row) for row in (value or [])]
            index = list(parent).index(node)
            for context in contexts:
                for original in group:
                    clone = deepcopy(original)
                    replace(clone, {}, [match[0], closing])
                    expand(clone, context)
                    parent.insert(index, clone)
                    index += 1
            for original in group: parent.remove(original)
            i = end + 1
        replace(parent, mapping)
    with ZipFile(template) as source:
        root = etree.fromstring(source.read("word/document.xml"), parser=etree.XMLParser(resolve_entities=False, no_network=True))
        expand(root, values)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, "w") as target:
            for item in source.infolist():
                target.writestr(item, etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                    if item.filename == "word/document.xml" else source.read(item.filename))


class TicketService:
    def __init__(self, account, template, folder, *, call_id=""):
        self.account, self.template, self.folder, self.call_id = account, Path(template) if template else None, Path(folder), call_id
        self.pending = None

    def prepare(self, issue_type, description, priority="Normal", contact_method="Phone"):
        customer = self.account._customer()
        if self.account.pending_recharge:
            raise ToolError("PENDING_SELECTION", "Please finish or cancel the pending package selection before drafting a ticket.")
        if not self.template or not self.template.is_file():
            raise ToolError("TEMPLATE_UNAVAILABLE", "The support-ticket template is not configured. No ticket was submitted.")
        priority = {"low": "Low", "normal": "Normal", "medium": "Normal", "high": "High", "urgent": "Urgent"}.get(priority.strip().casefold(), priority)
        contact_method = {"phone": "Phone", "phone call": "Phone", "call": "Phone", "email": "Email", "e-mail": "Email", "sms": "SMS"}.get(contact_method.strip().casefold(), contact_method)
        if priority not in {"Low", "Normal", "High", "Urgent"} or contact_method not in {"Phone", "Email", "SMS"}:
            raise ToolError("INVALID_TICKET", "Please clarify the priority and preferred contact method.")
        if not 5 <= len(description.strip()) <= 4000 or not 2 <= len(issue_type.strip()) <= 100:
            raise ToolError("INVALID_TICKET", "Please describe the issue before submitting a ticket.")
        related = list(self.account.db.support_tickets.find({"customer_id": customer["customer_id"]}, {"_id": 0,
            "ticketId": 1, "issueType": 1}).sort("created_at", -1).limit(3))
        self.pending = dict(ticketId="TKT-" + uuid.uuid4().hex[:12].upper(), customer_id=customer["customer_id"],
            name=customer.get("full_name", ""), email=customer.get("email", ""), phone=customer.get("phone", ""),
            issueType=issue_type.strip(), priority=priority, description=description.strip(), contactMethod=contact_method,
            hasAttachments=[], relatedTickets=[dict(ticketId=row["ticketId"], ticketSummary=row.get("issueType", "")) for row in related],
            call_id=self.call_id, data_type=customer.get("data_type", ""))
        return {"ok": True, "draft": {key: self.pending[key] for key in ("issueType", "priority", "description", "contactMethod")},
                "confirmation_required": True}

    def confirm(self):
        customer = self.account._customer()
        if not self.pending or self.pending["customer_id"] != customer["customer_id"]:
            raise ToolError("NO_PENDING_TICKET", "There is no ticket waiting for your confirmation.")
        row = dict(self.pending)
        row.update(status="Open", created_at=datetime.now(timezone.utc).isoformat())
        row["history"] = [dict(date=row["created_at"], status="Open", updatedBy="Maya", notes="Created after caller confirmation.")]
        path = self.folder / (row["ticketId"] + ".docx")
        render_ticket(self.template, path, row)
        row["document_file"] = path.name
        try:
            self.account.db.support_tickets.create_index("ticketId", unique=True)
            self.account.db.support_tickets.update_one({"ticketId": row["ticketId"]}, {"$setOnInsert": row}, upsert=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise ToolError("TICKET_WRITE_FAILED", "The ticket could not be registered. Please try again; no success is being claimed.") from None
        self.pending = None
        return {"ok": True, "ticket_id": row["ticketId"], "status": "Open", "document": str(path)}
