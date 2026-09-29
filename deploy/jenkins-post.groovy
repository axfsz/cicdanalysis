// 添加到 Declarative Pipeline 的 post 块。
// Jenkins 全局环境变量 CICD_ANALYSIS_URL 示例：http://cicdanalysis.cicd-analysis.svc:8080
post {
  always {
    script {
      withCredentials([string(credentialsId: 'cicdanalysis-webhook-secret', variable: 'CICD_SECRET')]) {
        sh(label: 'Notify cicdanalysis', script: '''
          set +x
          curl --fail --silent --show-error --max-time 10 \
            --retry 2 --retry-all-errors \
            -X POST "${CICD_ANALYSIS_URL}/api/v1/webhooks/jenkins" \
            -H "Authorization: Bearer ${CICD_SECRET}" \
            -H "Content-Type: application/json" \
            --data "{\\"job_name\\":\\"${JOB_NAME}\\",\\"build_number\\":${BUILD_NUMBER}}"
        ''')
      }
    }
  }
}
