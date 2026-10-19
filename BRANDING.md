# Google OAuth branding

This repository publishes its informational site from the `docs/` directory of `main` using GitHub Pages. No Google account data, credentials, or OAuth callbacks are hosted there.

For this personal deployment, use:

| Google Console field | Value |
| --- | --- |
| App name | `dms-integration` |
| Application home page | `https://hutm.github.io/dms-integration/` |
| Application privacy policy link | `https://hutm.github.io/dms-integration/privacy.html` |
| Application terms of service link | `https://hutm.github.io/dms-integration/terms.html` |
| Authorized domain | `hutm.github.io` |
| OAuth client type | Desktop app |
| Data Access scope | `https://www.googleapis.com/auth/tasks` |

Use the operator's actual email for support and developer contact. A logo is optional; do not add branding that suggests Google or DMS endorsement.

1. Confirm that all three public URLs load without authentication.
2. Fill the Branding fields, add the authorized domain, and save.
3. If Google requests domain ownership, add `https://hutm.github.io/dms-integration/` as a **URL-prefix** property in Google Search Console. Select HTML file or HTML tag verification and publish the exact verification file/tag Google supplies. This verification token is public; OAuth credentials are not.
4. Return to Audience and choose Publish app to switch from Testing to In production.
5. Personal use can qualify for Google's verification exemption. This does not make the app verified; an unverified-app warning and user cap can remain. Do not submit claims of public-app verification.
6. Reauthorize the desktop after publishing, then run sync and check the timer.

In production is a Google OAuth publishing setting. It does not expose your task list or turn the desktop tool into a hosted service. Google may change console validation and publishing requirements.

References: [OAuth policy and personal-use definition](https://developers.google.com/identity/protocols/oauth2/policies), [verification exemptions](https://support.google.com/cloud/answer/13464323), [branding fields](https://support.google.com/cloud/answer/15549049), [refresh token expiration](https://developers.google.com/identity/protocols/oauth2#expiration), [site ownership verification](https://support.google.com/webmasters/answer/9008080).
