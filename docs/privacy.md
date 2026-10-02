# Privacy

Voxframe runs on your own computer. Your recordings, your videos, your library
and your settings stay there. There is no account, no tracking and no
telemetry.

## When Voxframe uses the internet

Only in these cases, and only when you ask:

| when | what is sent | to |
|---|---|---|
| **First start**, when you click *Download* | a request for the speech and picture models (about 3.1 GB, or 750 MB for Lite) | Hugging Face, where the models are published |
| **Online image search**, only if you turn it on and add a key | words from a scene, to find pictures (with your key, for Pexels and Pixabay) | Pexels, Pixabay and Openverse |
| **Check for updates**, when you press the button | one request for the latest version number | GitHub |
| **The first time you use your own music track** (installed app only) | a request for the music tools that fit your track to the speech (about 90 MB, once) | PyPI (files.pythonhosted.org), where the Python libraries are published |

Nothing else leaves your computer. Your recording itself is never sent anywhere:
speech is recognised on your computer.

## Your keys

API keys you enter are stored in your own settings folder on your computer. They
are sent only to the service they belong to, and are never written into videos,
scene plans or logs.

## The app's local server

Voxframe shows itself in your web browser through a small server that only your
own computer can reach (`127.0.0.1`). Other computers on your network, and other
websites you visit, cannot use it.
