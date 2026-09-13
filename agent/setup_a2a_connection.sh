#!/usr/bin/env bash
# One-time setup: create the A2A connection from your Foundry project to the
# AWS-hosted agent at https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/
#
# ALTERNATIVE to Terraform: infra/a2a_connection.tf manages this same
# connection (plus the app-role grant this script does NOT do -- see the
# warning below). Pick one path, not both. Prefer the Terraform path unless
# you specifically don't want the connection in TF state; it's the one the
# README documents as authoritative and it handles the full identity chain.
#
#   AWS_A2A_TARGET   -> the SCF Compliance Assessment Agent's Entra-auth RPC
#                        endpoint, from its card's additionalInterfaces.
#                        Defaults to
#                        https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/entra/rpc
#   AWS_A2A_AUDIENCE -> the resource the connection requests a token FOR:
#                        the SCF API app's App ID URI,
#                        api://<scf-app-client-id>. (Resolved -- see the
#                        README "Connecting to an external A2A agent" section.)
#                        NOTE: the AWS authorizer's own expected `aud` is the
#                        BARE client-id GUID, not this api:// URI, because
#                        Entra stamps a v2.0 app-only token's aud as the GUID.
#                        The two intentionally differ; see the README.
#
# !!! IMPORTANT -- this script is NOT sufficient on its own !!!
# It only creates the *connection*. The Foundry managed identity also needs
# an "Agent.Invoke" APP ROLE (allowedMemberTypes=["Application"]) defined on
# the SCF app registration AND assigned to it -- without that the token has
# no valid `roles`/aud and the AWS /entra/rpc authorizer returns 401. The
# Terraform path (infra/a2a_connection.tf) does this for you; if you use this
# script instead, grant the app role separately (az ad app update --app-roles
# + a Graph appRoleAssignment). See the identity-chain diagram at the top of
# infra/a2a_connection.tf.
#
# Requires: azd CLI with the `ai` extension, logged in (`azd auth login`)
# and your Foundry project set as active.
set -euo pipefail

PROJECT_ENDPOINT="${AZURE_AI_PROJECT_ENDPOINT:?Set AZURE_AI_PROJECT_ENDPOINT first (see agent/.env.example)}"
AWS_A2A_TARGET="${AWS_A2A_TARGET:-https://ar4y22vewc.execute-api.us-east-1.amazonaws.com/entra/rpc}"
AWS_A2A_AUDIENCE="${AWS_A2A_AUDIENCE:?Set AWS_A2A_AUDIENCE to the SCF API App ID URI (api://<scf-app-client-id>) to request a token for -- see header}"
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
