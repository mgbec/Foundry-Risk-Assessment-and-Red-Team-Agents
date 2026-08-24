# Deploying the A2A test server

Disposable diagnostic, not part of the main Terraform-managed infra --
deployed imperatively via `az containerapp up`, which builds the image in
the cloud (no local Docker needed) and provisions a Container Apps
environment if one doesn't already exist.

```bash
az extension add --name containerapp --upgrade
az provider register --namespace Microsoft.App
```

```bash
cd a2a-test-server
az containerapp up --name a2a-test-server --resource-group rg-aisafety-redteam --location eastus2 --source . --ingress external --target-port 8080 --env-vars PORT=8080
```

Capture the FQDN Azure assigned and patch it into the agent card (so the
card's advertised URL matches reality):

```bash
$fqdn = az containerapp show --name a2a-test-server --resource-group rg-aisafety-redteam --query properties.configuration.ingress.fqdn -o tsv
az containerapp update --name a2a-test-server --resource-group rg-aisafety-redteam --set-env-vars PUBLIC_URL=https://$fqdn PORT=8080
```

Verify the card is being served correctly:

```bash
Invoke-RestMethod -Uri "https://$fqdn/.well-known/agent-card.json"
```

Then test outgoing A2A from Foundry against it:

```bash
cd ../agent
python a2a_caller_agent.py --target-url "https://$fqdn" --message "hello"
```

A reply containing `TEST-SERVER-CONFIRMED: you asked 'hello'` means the
full round trip -- card discovery, task execution, content delivery --
works against a genuine external A2A server, isolating the earlier
failures to Foundry-to-Foundry specifically.

## Tear down

```bash
az containerapp delete --name a2a-test-server --resource-group rg-aisafety-redteam --yes
```
