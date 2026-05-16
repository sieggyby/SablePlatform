"""Hand-written OpenAPI 3.1 document for the alert-triage MVP.

Three v1 routes only. Keep this short — if the surface grows, switch to
a code-generation path."""
from __future__ import annotations


def openapi_document() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "SablePlatform Alert Triage API",
            "version": "1.0.0",
            "description": (
                "Owner-issued, private-network API for alert triage. See "
                "docs/API_ALERT_TRIAGE_MVP.md."
            ),
        },
        "components": {
            "securitySchemes": {
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "sp_live token",
                },
            },
            "schemas": {
                "Alert": {
                    "type": "object",
                    "properties": {
                        "alert_id": {"type": "string"},
                        "org_id": {"type": "string"},
                        "alert_type": {"type": "string"},
                        "severity": {"type": "string"},
                        "title": {"type": "string"},
                        "body": {"type": "string", "nullable": True},
                        "status": {"type": "string"},
                        "created_at": {"type": "string"},
                        "acknowledged_at": {"type": "string", "nullable": True},
                        "acknowledged_by": {"type": "string", "nullable": True},
                        "resolved_at": {"type": "string", "nullable": True},
                    },
                    "required": ["alert_id", "alert_type", "severity", "title",
                                 "status", "created_at"],
                },
                "Error": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "message": {"type": "string"},
                    },
                    "required": ["error"],
                },
                "TriageResult": {
                    "type": "object",
                    "properties": {
                        "alert_id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "description": (
                                "Resulting state. One of 'acknowledged', "
                                "'already_acknowledged', 'already_resolved', "
                                "'resolved'."
                            ),
                        },
                    },
                    "required": ["alert_id", "status"],
                },
            },
        },
        "security": [{"bearer": []}],
        "paths": {
            "/v1/orgs/{org_id}/alerts": {
                "get": {
                    "summary": "List alerts for an org",
                    "parameters": [
                        {"name": "org_id", "in": "path", "required": True,
                         "schema": {"type": "string"}},
                        {"name": "status", "in": "query", "required": False,
                         "schema": {"type": "string", "default": "new"}},
                        {"name": "severity", "in": "query", "required": False,
                         "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "required": False,
                         "schema": {"type": "integer", "default": 50, "maximum": 200}},
                    ],
                    "responses": {
                        "200": {
                            "description": "List of alerts",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/Alert"},
                                    }
                                }
                            },
                        },
                        "401": {"description": "Missing or invalid token"},
                        "404": {"description": "Org not in token scope"},
                        "429": {"description": "Rate limit exceeded"},
                    },
                },
            },
            "/v1/alerts/{alert_id}/acknowledge": {
                "post": {
                    "summary": "Acknowledge an alert (idempotent)",
                    "parameters": [
                        {"name": "alert_id", "in": "path", "required": True,
                         "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Result of the triage operation",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/TriageResult"
                                    }
                                }
                            },
                        },
                        "401": {"description": "Missing or invalid token"},
                        "403": {"description": "Token lacks write_safe scope"},
                        "404": {"description": "Alert not found or out of scope"},
                        "429": {"description": "Rate limit exceeded"},
                    },
                },
            },
            "/v1/alerts/{alert_id}/resolve": {
                "post": {
                    "summary": "Resolve an alert (idempotent)",
                    "parameters": [
                        {"name": "alert_id", "in": "path", "required": True,
                         "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Result of the triage operation",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": "#/components/schemas/TriageResult"
                                    }
                                }
                            },
                        },
                        "401": {"description": "Missing or invalid token"},
                        "403": {"description": "Token lacks write_safe scope"},
                        "404": {"description": "Alert not found or out of scope"},
                        "429": {"description": "Rate limit exceeded"},
                    },
                },
            },
        },
    }
