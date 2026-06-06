/* USER CODE BEGIN Includes */
#include <string.h>
/* USER CODE END Includes */

/* USER CODE BEGIN PV */
UART_HandleTypeDef huart2;  /* 已在MX中初始化 */
volatile uint8_t rx_byte;   /* 中断接收缓冲区 */
/* USER CODE END PV */

/* USER CODE BEGIN 0 */
/* USER CODE END 0 */

int main(void)
{
  HAL_Init();
  SystemClock_Config();
  MX_GPIO_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */
  
  /* 启动中断接收：每收到1字节触发中断 */
  HAL_UART_Receive_IT(&huart2, (uint8_t *)&rx_byte, 1);
  
  /* USER CODE END 2 */

  while (1)
  {
    /* USER CODE BEGIN 3 */
    
    /* 检测 PD5 输入：如果高电平则点亮绿灯（视觉确认） */
    if (HAL_GPIO_ReadPin(GPIOD, GPIO_PIN_5) == GPIO_PIN_SET) 
    {
        HAL_GPIO_WritePin(GPIOD, GPIO_PIN_12, GPIO_PIN_SET);
    } 
    else 
    {
        HAL_GPIO_WritePin(GPIOD, GPIO_PIN_12, GPIO_PIN_RESET);
    }    
  }
  /* USER CODE END 3 */
}

/* USER CODE BEGIN 4 */
/**
  * @brief  UART中断接收完成回调
  *         收到1字节后立即回环发送，并翻转PD12给TAF反馈
  */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART2)
  {
    /* 回环：原样发回 */
    HAL_UART_Transmit(huart, (uint8_t *)&rx_byte, 1, 100);
    
    /* 翻转 PD12（绿灯），给 TAF 反馈 */
    HAL_GPIO_TogglePin(GPIOD, GPIO_PIN_12);
    
    /* 重新启动中断接收，准备接收下一字节 */
    HAL_UART_Receive_IT(huart, (uint8_t *)&rx_byte, 1);
  }
}
/* USER CODE END 4 */
