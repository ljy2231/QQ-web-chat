module.exports = {
  content: ['./templates/*.html', './static/*.js'],
  theme: { extend: {} },
  plugins: [],
  safelist: [
    'hidden', 'flex', 'block', 'opacity-50', 'bg-green-500', 'bg-yellow-500',
    'bg-red-500', 'text-red-500', 'text-green-600', 'text-yellow-600',
    'bg-blue-50', 'border-l-4', 'border-blue-500', 'justify-end', 'justify-start',
    'items-end', 'items-start', 'bg-blue-500', 'text-white', 'rounded-tr-sm',
    'rounded-tl-sm', 'bg-white', 'border-gray-200', 'text-gray-800'
  ]
};
