export const state = {
    config: null,
    apiEndpoint: null,
    // Bedrock/MCP は30秒超えが普通にあるのでデフォルトを上げる
    requestTimeoutMs: 60000,

    questionId: null, // = questionHash
    currentQuestion: null,

    recognition: null,
    recognizing: false,

    history: []
};
