# Security

A2RM is a personal fork of ARM 2.24.4 with no support channel and no release schedule. Only the current `main`
branch gets fixes.

A2RM is meant for a home network. Don't expose its web UI to the internet: it has ARM's simple login (change the
default `admin` / `password`), and the home page is visible without logging in, as in ARM. The buttons in phone
notifications are signed links, so they work without a login but can't be forged.

If a security problem is in ARM itself, report it to ARM: see
[ARM's security policy](https://github.com/automatic-ripping-machine/automatic-ripping-machine/security/policy).
