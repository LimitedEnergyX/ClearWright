-- ClearWright Protocol v0.1: Packet Registry Schema
-- ClearWright project
--
-- This table indexes the state of clearance packets.
-- The durable packet artifact (filesystem record) remains the source of truth.
-- This database is reconstructable from packet artifacts at any time.
--
-- See docs/CLEARWRIGHT_PROTOCOL.md for rationale.

CREATE TABLE IF NOT EXISTS clearance_packet (
    -- Identity
    packet_id        TEXT PRIMARY KEY,
    packet_type      TEXT NOT NULL,
    title            TEXT NOT NULL,
    requesting_agent TEXT NOT NULL,

    -- Timestamps (ISO 8601 UTC)
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    -- Lifecycle state
    status           TEXT NOT NULL DEFAULT 'RTA'
                     CHECK (status IN (
                         'RTA',
                         'IN_REVIEW',
                         'RFI_PENDING',
                         'CTA',
                         'IN_PROGRESS',
                         'DTA',
                         'DONE',
                         'FAILED',
                         'SUPERSEDED'
                     )),

    -- Classification
    risk_level       TEXT,
    scope            TEXT,

    -- Request content
    intent           TEXT,
    proposed_action  TEXT,

    -- JSON blobs (stored as TEXT; validated at application layer)
    inputs_json      TEXT,    -- Agent inputs and context
    review_json      TEXT,    -- Reviewer notes and interim decisions
    rfi_json         TEXT,    -- Request-for-information rounds
    decision_json    TEXT,    -- Final CTA or DTA decision record
    audit_json       TEXT,    -- Full lifecycle audit trail

    -- Claim fields (set atomically with the CTA -> IN_PROGRESS transition)
    claimed_by       TEXT,    -- Agent or system that claimed execution rights
    claimed_at       TEXT,    -- ISO 8601 UTC timestamp of claim
    claim_expires_at TEXT,    -- ISO 8601 UTC expiry; NULL means no expiry

    -- Authority and coordination fields (v0.1; see docs/AUTHORITY_MODEL.md)
    -- Allowed values are validated at the application layer, not via CHECK
    -- constraints, to preserve schema extensibility without table recreation.
    -- Allowed authority_class: OPERATOR | ORCHESTRATOR | REVIEWER | WORKER | OBSERVER | POLICY_ENGINE
    authority_class      TEXT,  -- Authority tier of the requesting actor
    -- Allowed clearance_class: READ_ONLY | DOCS_ONLY | BRANCH_CODE | QUEUE_MOVE | EXECUTION_CANDIDATE | HUMAN_REQUIRED
    clearance_class      TEXT,  -- Scope and risk level of the clearance being requested
    -- Allowed priority_class: LOW | NORMAL | HIGH | URGENT
    priority_class       TEXT,  -- Scheduling priority of this packet
    channel_id           TEXT,  -- Logical workflow channel this packet occupies
    -- Allowed channel_state: CLEAR | BUSY | BLOCKED | STALE | ESCALATED
    channel_state        TEXT,  -- Readiness state of the channel at time of request

    -- Clearance and decision provenance
    cleared_by           TEXT,  -- Actor that issued the CTA (agent, reviewer, policy, or operator)
    denied_by            TEXT,  -- Actor that issued the DTA
    delegated_by         TEXT,  -- Higher-authority actor that authorized this clearance delegation
    clearance_expires_at TEXT,  -- ISO 8601 UTC expiry of the CTA lease (distinct from claim_expires_at)

    -- Escalation
    escalation_required  INTEGER DEFAULT 0,  -- 1 = operator approval required; 0 = may be cleared autonomously
    escalation_reason    TEXT,  -- Why escalation is required, if escalation_required = 1

    -- Backpressure
    backpressure_json    TEXT,  -- JSON object: backpressure state and metrics at time of RTA

    -- Artifact linkage
    source_path      TEXT,    -- Path to durable packet artifact on disk
    packet_hash      TEXT     -- SHA-256 of packet artifact for integrity verification
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_clearance_packet_status
    ON clearance_packet (status);

CREATE INDEX IF NOT EXISTS idx_clearance_packet_requesting_agent
    ON clearance_packet (requesting_agent);

CREATE INDEX IF NOT EXISTS idx_clearance_packet_created_at
    ON clearance_packet (created_at);

CREATE INDEX IF NOT EXISTS idx_clearance_packet_claimed_by
    ON clearance_packet (claimed_by);

CREATE INDEX IF NOT EXISTS idx_clearance_packet_packet_hash
    ON clearance_packet (packet_hash);

-- Trigger: keep updated_at current on any row change.
-- WHEN guard: only fires when updated_at has not already been set by the caller,
-- preventing recursion if SQLite recursive triggers are enabled.
CREATE TRIGGER IF NOT EXISTS trg_clearance_packet_updated_at
    AFTER UPDATE ON clearance_packet
    FOR EACH ROW
    WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE clearance_packet
       SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
     WHERE packet_id = OLD.packet_id;
END;
