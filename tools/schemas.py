from __future__ import annotations

__all__ = [
    "GET_STATUS_SCHEMA",
    "TRIGGER_CYCLE_SCHEMA",
    "GET_PLAN_SCHEMA",
    "REPLAN_SCHEMA",
    "RECORD_OUTCOME_SCHEMA",
    "GET_LEARNED_INSIGHTS_SCHEMA",
]

GET_STATUS_SCHEMA = {
    "type": "object",
    "description": (
        "Retrieve current daily_life status, active agenda focus, Brain cognitive health metrics, "
        "and connected adapter state for the agent profile."
    ),
    "properties": {
        "date_str": {
            "type": "string",
            "description": "Optional date string formatted YYYY-MM-DD. Defaults to today.",
        }
    },
    "required": [],
    "additionalProperties": False,
}

TRIGGER_CYCLE_SCHEMA = {
    "type": "object",
    "description": (
        "Trigger idempotent daily_life planning or review cycle execution. Coordinates interoception, "
        "morning briefing, and calendar time-blocking."
    ),
    "properties": {
        "force": {
            "type": "boolean",
            "description": "Force execution even if daily plan was already generated today.",
            "default": False,
        },
        "date_str": {
            "type": "string",
            "description": "Optional target date YYYY-MM-DD.",
        },
    },
    "required": [],
    "additionalProperties": False,
}

GET_PLAN_SCHEMA = {
    "type": "object",
    "description": (
        "Get active daily_life plan, scheduled tasks, and calendar time-blocks for today or a specific date."
    ),
    "properties": {
        "date_str": {
            "type": "string",
            "description": "Target date string YYYY-MM-DD.",
        }
    },
    "required": [],
    "additionalProperties": False,
}

REPLAN_SCHEMA = {
    "type": "object",
    "description": (
        "Request dynamic replanning of remaining daily tasks due to unexpected schedule conflicts, "
        "new urgent requests, or elevated Brain fatigue."
    ),
    "properties": {
        "reason": {
            "type": "string",
            "description": "Reason for replanning (e.g. ad-hoc task interruption, high fatigue, calendar shift).",
        }
    },
    "required": ["reason"],
    "additionalProperties": False,
}

RECORD_OUTCOME_SCHEMA = {
    "type": "object",
    "description": (
        "Record task execution outcome telemetry and business metrics into daily_life learning ledger."
    ),
    "properties": {
        "activity_type": {
            "type": "string",
            "description": "Category of activity (e.g. web_research, threads_publish, data_scraping).",
        },
        "estimated_duration_min": {
            "type": "integer",
            "description": "Estimated duration in minutes.",
        },
        "actual_duration_min": {
            "type": "integer",
            "description": "Actual duration spent in minutes.",
        },
        "status": {
            "type": "string",
            "enum": ["success", "failed", "cancelled"],
            "description": "Terminal status of the task.",
        },
        "outcome_metric_name": {
            "type": "string",
            "description": "Optional business outcome metric name (e.g. engagement_likes, conversion_rate).",
        },
        "outcome_metric_value": {
            "type": "number",
            "description": "Numeric outcome value achieved.",
        },
        "error_reason": {
            "type": "string",
            "description": "Error or failure description if failed.",
        },
    },
    "required": ["activity_type", "estimated_duration_min", "actual_duration_min", "status"],
    "additionalProperties": False,
}

GET_LEARNED_INSIGHTS_SCHEMA = {
    "type": "object",
    "description": (
        "Retrieve learned_preferences, calibrated duration multipliers, and published shared insights "
        "derived from historical telemetry."
    ),
    "properties": {
        "activity_type": {
            "type": "string",
            "description": "Optional activity category filter.",
        }
    },
    "required": [],
    "additionalProperties": False,
}
