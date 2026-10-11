# 🎵 Self Music - Personal Music System

- Preview: https://music.icodeq.com

![Screenshot (616)](https://img.onmicrosoft.cn/zkeq/20250817125324565.png)
![Screenshot (617)](https://img.onmicrosoft.cn/zkeq/20250817125724096.png)
![Screenshot (618)](https://img.onmicrosoft.cn/zkeq/20250817125723943.png)
![Screenshot (619)](https://img.onmicrosoft.cn/zkeq/20250817125723887.png)
![Screenshot (621)](https://img.onmicrosoft.cn/zkeq/20250817125729741.png)
![Screenshot (620)](https://img.onmicrosoft.cn/zkeq/20250817125727225.png)
![Screenshot (624)](https://img.onmicrosoft.cn/zkeq/20250817125723840.png)
![Screenshot (623)](https://img.onmicrosoft.cn/zkeq/20250817125723806.png)

<div align="center">

**🌟 A modern private music management and playback system, supporting playlists, lyrics synchronization, and multi-device access. (A private music client that supports one-click import of NetEase Cloud Music metadata and batch scraping.)**

[🚀 One-Click Deploy](#-one-click-deploy) •
[🚀 Quick Start](#-quick-start) •
[✨ Features](#-features) •
[🛠️ Tech Stack](#️-tech-stack) •
[🤝 Contributing](#-contributing)

</div>

---

## 📖 Introduction

Self Music is a music management and playback system for individuals and small teams. It provides an elegant UI and a smooth playback experience, supports playlist management, lyrics synchronization, artist/album data browsing, and has a built-in admin backend (JWT authentication), ready to use out of the box.

## 🚀 One-Click Deploy

We offer three convenient deployment methods: `Docker Run`, `Docker Compose`, and [BT Panel Deployment](#-quick-start).

### Method 1: Using Docker Run (Recommended)

This is the easiest and fastest way to get started. You can run the entire application with just one command. (Note that due to data persistence requirements, the directory in which the command is started must be the same each time it is executed. It is recommended to execute it in the `~` directory).

**Recommended for servers in China (using accelerated mirror):**
```bash
docker run -d \
  --name self-music-app \
  --restart unless-stopped \
  -p 6230:80 \
  -v "$(pwd)/music_data":/data \
  docker.cnb.cool/onmicrosoft/self-music:latest
```

**For overseas servers or local development (using official Docker Hub image):**
```bash
docker run -d \
  --name self-music-app \
  --restart unless-stopped \
  -p 6230:80 \
  -v "$(pwd)/music_data":/data \
  zkeq/self-music:latest
```
- After starting, the application will be accessible at `http://localhost:6230`.
- All data (database, configuration files, music files) will be saved in the `music_data` folder in the current directory (restart the project after changing the password to update it automatically).
- Default administrator account: `admin` / `admin123`.

### Method 2: Using Docker Compose

If you need more flexible configuration or want to do secondary development, you can use `docker-compose`.

1.  **Clone this project**
    ```bash
    git clone https://github.com/zkeq/Self-music.git
    cd Self-music
    ```

2.  **Start the service**
    ```bash
    docker-compose up -d
    ```
- After starting, the application will be accessible at `http://localhost:8080`.

---

## ✨ Features

- **Modern UI**: A beautiful interface based on shadcn/ui + Tailwind CSS 4, with support for light and dark theme switching.
- **Complete Playback Experience**: Supports playback queue, LRC lyrics synchronized scrolling, and random/loop playback modes.
- **Multi-dimensional Browsing**: Discover music by artist, album, playlist, mood, and more.
- **Powerful Admin Panel**: Built-in admin system with full CRUD operations for songs, artists, playlists, etc.
- **Data Persistence**: All data is persisted via volumes, making backups and migrations easy.

---

## 🛠️ Tech Stack

- **Backend**: FastAPI (Python) + SQLite
- **Frontend**: Next.js (React) + TypeScript + Tailwind CSS
- **Containerization**: Docker + Nginx

---

## 🔧 Local Development

If you need to do secondary development, please set up your local environment as follows.

1.  **Prerequisites**
    - Python 3.8+
    - Node.js 18+
    - pnpm (recommended)

2.  **Clone the project**
    ```bash
    git clone https://github.com/zkeq/Self-music.git
    cd Self-music
    ```

3.  **Start the backend**
    ```bash
    cd backend
    pip install -r requirements.txt
    uvicorn main:app --reload --host 0.0.0.0 --port 8000
    ```
    - The backend service runs at `http://localhost:8000`

4.  **Start the frontend**
    ```bash
    cd ../frontend
    pnpm install
    pnpm dev
    ```
    - The frontend service runs at `http://localhost:3000`

---

### 🎯 Core Highlights

- 🎨 Modern Design: Beautiful interface based on shadcn/ui + Tailwind CSS 4
- 📱 Responsive Layout: Seamlessly adapts to desktop and mobile
- 🔐 Secure Authentication: JWT-based login for the admin panel
- 🎧 Playback Experience: HTML5 audio + lyrics (LRC) parsing and synchronized scrolling
- 🎛️ Playback Controls: Progress, volume, shuffle, loop, previous/next
- 🌓 Theme Switching: Built-in dark mode (next-themes)

---

## ✨ Features

### 🎛 Playback & Experience
- 🎵 Playlists: Supports playback queue, playing from a list, and jumping to a specific song
- 📝 Lyrics Support: LRC/plain text parsing, supports full-screen lyrics with scrolling highlight
- 🔁 Playback Modes: Shuffle / list loop / single loop
- 💾 Local Storage: Playlists and state are persisted in localStorage

### 🎭 Browsing & Categorization
- 👤 Artists: List and details, songs/albums by the artist
- 💿 Albums: List and details, songs within an album
- 📂 Playlists: Browse and play public playlists
- 😊 Mood/Atmosphere: Display and filter by mood (backend support)

### 🛠 Admin Panel (/admin)
- ✅ Artists/Albums/Songs/Moods/Playlists: Full CRUD
- 🔃 Playlist Reordering: Supports custom ordering (returns in the saved order)
- 📦 Batch Import: Batch write song/album/artist information (including lyrics and audio URLs)
- 👤 Default Admin: `admin / admin123`

### 🔐 Security Features
- 🛡️ JWT Authentication: Bearer Token for protected admin APIs
- 🌐 CORS Settings: Allows all origins by default

---

## 🚀 Quick Start

### ☁️ Cloud Server Deployment (Backend) [Using BT Panel]

1. Upload the `backend` folder to the `/root` directory on your server.

![478884422-4da357c8-ba77-4bfb-bc1f-fecd4f122349](https://img.onmicrosoft.cn/zkeq/20250830095833542.png)


2. Modify the configuration items `jwt_secret` and the password of the `admin` administrator in `backend/config.yaml`.

![483839127-2a5497bd-2b1e-4923-8806-6e215f97b9ea](https://img.onmicrosoft.cn/zkeq/20250830095846596.png)


3. Open Pagoda Panel -> Websites -> `Python Project` -> `Add Site`.
   Create a new virtual environment.
   ![image-20250731101721616](https://img.onmicrosoft.cn/zkeq/20250731101721697.png)

4. Fill out the form as shown below.
   ![image-20250731101757603](https://img.onmicrosoft.cn/zkeq/20250731101757694.png)

5. After clicking confirm, the project will create the virtual environment and install dependencies. Wait for the installation to complete.

6. Click on settings to view the project log.
   ![image-20250731101900749](https://img.onmicrosoft.cn/zkeq/20250731101900862.png)

7. If you get an error that a dependency is not found, click on `Terminal` in the `Actions` column and manually enter `pip install xxx (package name)`. If it says the port is occupied, change to an unused port in `main.py`.
   ![image-20250731102037862](https://img.onmicrosoft.cn/zkeq/20250731102037989.png)

8.  Request the service port to check the running status. (If you see this string, the service is running normally).
    ![image-20250731102123918](https://img.onmicrosoft.cn/zkeq/20250731102123995.png)

9. The backend deployment is complete. You can now set up a reverse proxy with a domain on your CDN to go live.

### ☁️ Vercel Deployment (Frontend)

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https%3A%2F%2Fgithub.com%2Fzkeq%2FSelf-Music%2Ftree%2Fmain%2Ffrontend&env=NEXT_PUBLIC_API_URL&envDescription=%E5%90%8E%E7%AB%AF%E9%A1%B9%E7%9B%AE%E5%9C%B0%E5%9D%80%EF%BC%88%E7%A4%BA%E4%BE%8B%3A%20https%3A%2F%2Fmusic-api.onmicrosoft.cn%2Fapi%EF%BC%89%EF%BC%9A&project-name=self-music&repository-name=self-music)

- Follow the steps to deploy successfully.
<img width="2093" height="1284" alt="image" src="https://github.com/user-attachments/assets/f6a370cc-b9e6-47d7-a7d2-20d83951bbaa" />

---

## 📁 Project Structure

```
Self-Music/
├── backend/                 # FastAPI + SQLite
│   ├── main.py              # API entrypoint (includes admin routes)
│   ├── user.py              # Public-facing user APIs
│   ├── music.db             # SQLite database
│   └── requirements.txt     # Python dependencies
├── frontend/                # Next.js + TypeScript + Tailwind CSS 4
│   ├── src/
│   │   ├── app/             # App routes (/play, /artists, /admin, etc.)
│   │   ├── components/      # UI components, player, lyrics, panels
│   │   ├── lib/             # API client, store, utils
│   │   └── styles/          # Global styles and themes
│   └── package.json         # Frontend dependencies and scripts
├── AGENTS.md                # Repository contribution and development guidelines (Chinese)
├── README.md                # Chinese README (this file)
└── README.en.md             # English README
```

---

## 📚 API Overview (Excerpt)

- Public APIs (no login required)
  - `GET /api/songs`: Get songs with pagination
  - `GET /api/songs/{id}`: Song details
  - `GET /api/songs/{id}/stream`: Audio stream
  - `GET /api/artists`, `/api/artists/{id}`, `/api/artists/{id}/songs`, `/api/artists/{id}/albums`
  - `GET /api/albums`, `/api/albums/{id}`, `/api/albums/{id}/songs`
  - `GET /api/playlists`, `/api/playlists/{id}`

- Admin APIs (requires Bearer Token)
  - `POST /api/auth/login`: Admin login
  - `GET/POST/PUT/DELETE /api/admin/{artists|albums|songs|moods|playlists}`
  - `PUT /api/admin/playlists/{id}/reorder`: Reorder playlist
  - `POST /api/admin/upload`: Upload audio file
  - `POST /api/admin/import/*`: Batch import and duplicate check

> See the Swagger docs at `/docs` after running the backend for details.

---

## 🤝 Contributing

- Before submitting, ensure: builds pass, `pnpm lint` passes, API runs locally
- Follow the repository conventions (see `AGENTS.md`)
- Contributions are welcome: Bug reports, feature proposals, documentation improvements, internationalization support

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).


---

## Special Thanks

Thanks to [@haorwen](https://github.com/haorwen) for providing the NetEase Cloud Music metadata API service for this project.

---

<div align="center">

**⭐ If this project helps you, please give it a star! ⭐**

Made with ❤️ for music lovers.

</div>
