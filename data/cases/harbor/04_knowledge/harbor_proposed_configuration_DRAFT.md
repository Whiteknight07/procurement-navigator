HARBOR REGIONAL HEALTH - PROPOSED DEPLOYMENT (DRAFT v0.2, Parsa, Oct 20)
Storage: AWS us-east-1 (Virginia). Backups us-east-2.
Inference: OpenAI via Azure OpenAI East US (proposed, needs Azure BAA confirmation). Anthropic fallback OFF.
PHI redaction (names, MRNs, DOB) before prompt: proposed ON (beta feature).
App logs: 7 days (proposed healthcare override). Prompt/response storage: 30 days.
Ticket text copies retained for 30 days then deleted.
Auth: SAML 2.0 SSO, JIT provisioning; SCIM not available.
DRAFT - not deployed, not approved by Legal or Engineering.
