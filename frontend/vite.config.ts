import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import { codecovVitePlugin } from '@codecov/vite-plugin'
import { brotliCompressSync, constants, gzipSync } from 'node:zlib'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

// Deja junto a cada JS, CSS y SVG de dist/assets su versión comprimida (.br y .gz). El servidor (web/app.py,
// _AssetsInmutables) las sirve tal cual según el Accept-Encoding: se comprimen una vez, al construir, con el nivel
// máximo, y no a cada petición con el nivel 6 del middleware. Los woff2 y las imágenes ya vienen comprimidos. Solo se
// conserva la que ocupa menos que el original.
function precomprimirAssets(): Plugin {
  const comprimible = /\.(js|css|svg)$/
  return {
    name: 'precomprimir-assets',
    apply: 'build',
    writeBundle(opciones, paquete) {
      for (const [nombre, salida] of Object.entries(paquete)) {
        if (!comprimible.test(nombre) || !opciones.dir) continue
        const datos = Buffer.from(salida.type === 'chunk' ? salida.code : salida.source)
        if (datos.length < 1024) continue
        const variantes: [string, Buffer][] = [
          ['.br', brotliCompressSync(datos, {
            params: { [constants.BROTLI_PARAM_QUALITY]: 11, [constants.BROTLI_PARAM_SIZE_HINT]: datos.length },
          })],
          ['.gz', gzipSync(datos, { level: 9 })],
        ]
        for (const [extension, comprimido] of variantes) {
          if (comprimido.length < datos.length) writeFileSync(join(opciones.dir, nombre + extension), comprimido)
        }
      }
    },
  }
}

export default defineConfig({
  plugins: [
    react(),
    precomprimirAssets(),
    // Sube el tamaño del bundle a Codecov solo donde hay CODECOV_TOKEN (el CI); si la subida falla, el build sigue.
    codecovVitePlugin({
      enableBundleAnalysis: Boolean(process.env.CODECOV_TOKEN),
      bundleName: 'frontend',
      uploadToken: process.env.CODECOV_TOKEN,
      telemetry: false,
    }),
  ],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:9494',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Las secciones van cada una en su chunk (AppShell): el mayor ronda los 220 kB (React). Que vuelva a avisar si
    // alguno crece mucho, o si una importación estática vuelve a meter una sección en el principal.
    chunkSizeWarningLimit: 300,
    rolldownOptions: {
      output: {
        codeSplitting: {
          // React y su planificador en un chunk propio, que cambia mucho menos que la app.
          groups: [{ name: 'react', test: /[\\/]node_modules[\\/](react|react-dom|scheduler)[\\/]/ }],
        },
      },
    },
  },
})
