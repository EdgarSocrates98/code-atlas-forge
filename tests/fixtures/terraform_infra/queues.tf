# count/for_each → multiplicity declarada "dynamic"; nenhuma expansão
# fabricada (nenhum aws_sqs_queue.workers[0], [1]… é emitido).

resource "aws_sqs_queue" "workers" {
  count = var.replicas
  name  = "worker-${count.index}"
}

resource "aws_cloudwatch_metric_alarm" "depth" {
  for_each = { high = 100, low = 10 }

  alarm_name = "depth-${each.key}"
  dimensions = {
    QueueName = aws_sqs_queue.workers[0].name
  }
}
