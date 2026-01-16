export const state = {
  config: null,
  apiEndpoint: null,
  // Bedrock/MCP は15秒超えが普通にあるのでデフォルトを上げる
  requestTimeoutMs: 45000,

  questionId: null,      // = questionHash
  currentQuestion: null,

  recognition: null,
  recognizing: false,

  history: []
};
