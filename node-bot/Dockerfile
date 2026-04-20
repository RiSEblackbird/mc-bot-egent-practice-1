FROM node:22-bookworm-slim

WORKDIR /app/node-bot

COPY node-bot/package*.json ./
RUN npm ci

COPY node-bot/ ./

EXPOSE 8765

CMD ["npm", "run", "dev"]
