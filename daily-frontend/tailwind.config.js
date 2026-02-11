/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        neon: {
          cyan: '#00ffff',
          magenta: '#ff00ff',
          green: '#00ff41',
          amber: '#ffc800',
          pink: '#ff2d95',
          blue: '#00d4ff',
        },
      },
      boxShadow: {
        'neon-cyan': '0 0 5px rgba(0,255,255,0.3), 0 0 20px rgba(0,255,255,0.1)',
        'neon-cyan-lg': '0 0 10px rgba(0,255,255,0.4), 0 0 40px rgba(0,255,255,0.15)',
        'neon-magenta': '0 0 5px rgba(255,0,255,0.3), 0 0 20px rgba(255,0,255,0.1)',
        'neon-green': '0 0 5px rgba(0,255,65,0.3), 0 0 20px rgba(0,255,65,0.1)',
        'neon-amber': '0 0 5px rgba(255,200,0,0.3), 0 0 20px rgba(255,200,0,0.1)',
        'neon-pink': '0 0 5px rgba(255,45,149,0.3), 0 0 20px rgba(255,45,149,0.1)',
      },
    },
  },
  plugins: [],
}
