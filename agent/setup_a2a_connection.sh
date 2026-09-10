#!/usr/bin/env bash
# One-time setup: create the A2A connection from your Foundry project to the
# AWS-hosted agent at https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/
#
# Fill in the two placeholders below from the agent card
# (GET https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/.well-known/agent-card.json)
# and from whoever configured the AWS-side JWT authorizer:
#
#   AWS_A2A_TARGET   -> confirmed from the SCF Compliance Assessment Agent's
#                        card (additionalInterfaces): defaults to
#                        https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/entra/rpc
#   AWS_A2A_AUDIENCE -> the Application ID URI / client ID the AWS Lambda
#                        authorizer validates as `aud` in the JWT. NOT
#                        published in the agent card (the card's "entra"
#                        scheme is generic OIDC against the multi-tenant
#                        `common` endpoint) -- check the Lambda authorizer
#                        code/config on the AWS side for this value.
#
# Requires: azd CLI with the `ai` extension, logged in (`azd auth login`)
# and your Foundry project set as active.
set -euo pipefail

PROJECT_ENDPOINT="${AZURE_AI_PROJECT_ENDPOINT:?Set AZURE_AI_PROJECT_ENDPOINT first (see agent/.env.example)}"
AWS_A2A_TARGET="${AWS_A2A_TARGET:-https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/entra/rpc}"
AWS_A2A_AUDIENCE="${AWS_A2A_AUDIENCE:?Set AWS_A2A_AUDIENCE to the Entra App ID URI the AWS side validates}"
CONNECTION_NAME="${A2A_CONNECTION_NAME:-aws-redteam-agent-a2a}"

azd ai project set "$PROJECT_ENDPOINT"

# project-managed-identity: the Foundry PROJECT's own system-assigned
# identity is the caller. Simplest starting point.
#
# Swap --auth-type to agentic-identity if you'd rather the specific hosted
# agent's own dedicated Entra identity be the caller (finer-grained, but
# only applies once that agent is deployed as a Hosted agent).
azd ai connection create "$CONNECTION_NAME" \
  --kind remote-a2a \
  --target "$AWS_A2A_TARGET" \
  --auth-type project-managed-identity \
  --audience "$AWS_A2A_AUDIENCE"

echo "Created A2A connection '$CONNECTION_NAME'. Set A2A_CONNECTION_NAME=$CONNECTION_NAME in your .env."
