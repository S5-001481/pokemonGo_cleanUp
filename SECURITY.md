# Security and privacy

## Reporting a vulnerability

Please report security issues privately to the repository maintainers rather than
opening a public issue containing exploit details or personal data.

## Local data

Captured screens and metadata remain on the user's computer. The default
`data/screenshots/` directory is Git-ignored, but users are responsible for
protecting and deleting their own captures. A device serial number is stored in
each metadata sidecar.

The project does not request Pokémon GO credentials, access game accounts, call
private game APIs, or inspect network traffic. A future change that alters these
boundaries requires explicit security and privacy review.
