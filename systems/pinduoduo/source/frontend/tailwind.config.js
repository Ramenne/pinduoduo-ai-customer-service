/**
 * Tailwind CSS 配置：拼多多自动回复系统前端
 * 设计目标：与参考项目 xianyu-auto-reply-wangpan 保持一致的视觉风格——
 *   蓝白主色调 + slate 中性色，支持暗黑模式（class 策略），支持主题色修改（规范 25/26）。
 * 说明：主题色通过 CSS 变量 --theme-primary-* 注入到 Tailwind 的 primary 调色板，
 *   由 utils/theme.js 在切换主题色时改写这些变量，从而让所有 primary 工具类随主题色联动。
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  // 暗黑模式采用 class 策略：在 <html> 上加 .dark 即生效（规范 25）
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // 主色调（蓝色系）：色值取自 CSS 变量，便于主题色动态切换（规范 26）
        primary: {
          50: 'rgb(var(--theme-primary-50) / <alpha-value>)',
          100: 'rgb(var(--theme-primary-100) / <alpha-value>)',
          200: 'rgb(var(--theme-primary-200) / <alpha-value>)',
          300: 'rgb(var(--theme-primary-300) / <alpha-value>)',
          400: 'rgb(var(--theme-primary-400) / <alpha-value>)',
          500: 'rgb(var(--theme-primary-500) / <alpha-value>)',
          600: 'rgb(var(--theme-primary-600) / <alpha-value>)',
          700: 'rgb(var(--theme-primary-700) / <alpha-value>)',
          800: 'rgb(var(--theme-primary-800) / <alpha-value>)',
          900: 'rgb(var(--theme-primary-900) / <alpha-value>)',
        },
      },
      animation: {
        'fade-in': 'fadeIn 0.15s ease-out',
        'slide-up': 'slideUp 0.2s ease-out',
        'scale-in': 'scaleIn 0.15s ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        scaleIn: {
          '0%': { opacity: '0', transform: 'scale(0.95)' },
          '100%': { opacity: '1', transform: 'scale(1)' },
        },
      },
    },
  },
  plugins: [],
}
