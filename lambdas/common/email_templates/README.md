# Email templates

Reusable, email-safe templates for the Xomforms Distribution phase (form-invite
sends). Rendered by the send-Lambda and passed to SES `SendEmail`
(HTML + text bodies).

## Files
- `form_invite.html` — responsive, inline-styled HTML (tables, bulletproof CTA,
  MSO/Outlook fallbacks). No external CSS/JS/images.
- `form_invite.txt` — plain-text fallback for the multipart alternative.

## Placeholders
Both files use `{{...}}` tokens. Substitute all of them before sending:

| Token              | Example                                  |
|--------------------|------------------------------------------|
| `{{recipientName}}`| `Sam` (fall back to `there` if unknown)  |
| `{{senderName}}`   | `Dominick`                               |
| `{{formTitle}}`    | `Team offsite — pick a weekend`          |
| `{{formUrl}}`      | `https://xomforms.xomware.com/p/abc123`  |
| `{{year}}`         | `2026`                                   |

## Sending config (from Terraform, via SSM)
- From address: SSM `/xomforms/ses/FROM_ADDRESS` → `noreply@xomforms.xomware.com`
- Configuration set: SSM `/xomforms/ses/CONFIGURATION_SET` → `xomforms-invites`

Always HTML-escape user-supplied values (`formTitle`, `recipientName`,
`senderName`) before substituting into `form_invite.html`.
