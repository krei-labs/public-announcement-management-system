"""
System Monitor for Raspberry Pi
Monitors CPU temperature, disk usage, and other system metrics
"""

import psutil
import os
from datetime import datetime

class SystemMonitor:
    def __init__(self):
        self.thermal_file = '/sys/class/thermal/thermal_zone0/temp'
    
    def get_cpu_temperature(self):
        """Get CPU temperature (Raspberry Pi specific)"""
        try:
            if os.path.exists(self.thermal_file):
                with open(self.thermal_file, 'r') as f:
                    temp = float(f.read()) / 1000.0
                return round(temp, 1)
            else:
                
                return 0.0
        except Exception as e:
            print(f"Error reading CPU temperature: {e}")
            return 0.0
    
    def get_disk_usage(self):
        """Get disk usage statistics"""
        try:
            disk = psutil.disk_usage('/')
            return {
                'total': self._bytes_to_gb(disk.total),
                'used': self._bytes_to_gb(disk.used),
                'free': self._bytes_to_gb(disk.free),
                'percent': disk.percent
            }
        except Exception as e:
            print(f"Error reading disk usage: {e}")
            return {
                'total': 0,
                'used': 0,
                'free': 0,
                'percent': 0
            }
    
    def get_memory_usage(self):
        """Get RAM usage statistics"""
        try:
            mem = psutil.virtual_memory()
            return {
                'total': self._bytes_to_gb(mem.total),
                'used': self._bytes_to_gb(mem.used),
                'available': self._bytes_to_gb(mem.available),
                'percent': mem.percent
            }
        except Exception as e:
            print(f"Error reading memory usage: {e}")
            return {
                'total': 0,
                'used': 0,
                'available': 0,
                'percent': 0
            }
    
    def get_cpu_usage(self):
        """Get CPU usage percentage"""
        try:
            return psutil.cpu_percent(interval=1)
        except Exception as e:
            print(f"Error reading CPU usage: {e}")
            return 0.0
    
    def get_uptime(self):
        """Get system uptime"""
        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            uptime = datetime.now() - boot_time
            
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            return {
                'days': days,
                'hours': hours,
                'minutes': minutes,
                'seconds': seconds,
                'formatted': f"{days}d {hours}h {minutes}m"
            }
        except Exception as e:
            print(f"Error reading uptime: {e}")
            return {
                'days': 0,
                'hours': 0,
                'minutes': 0,
                'seconds': 0,
                'formatted': '0d 0h 0m'
            }
    
    def get_network_info(self):
        """Get network statistics"""
        try:
            net_io = psutil.net_io_counters()
            return {
                'bytes_sent': self._bytes_to_mb(net_io.bytes_sent),
                'bytes_recv': self._bytes_to_mb(net_io.bytes_recv),
                'packets_sent': net_io.packets_sent,
                'packets_recv': net_io.packets_recv
            }
        except Exception as e:
            print(f"Error reading network info: {e}")
            return {
                'bytes_sent': 0,
                'bytes_recv': 0,
                'packets_sent': 0,
                'packets_recv': 0
            }
    
    def get_status(self):
        """Get comprehensive system status"""
        cpu_temp = self.get_cpu_temperature()
        
        
        if cpu_temp < 60:
            temp_status = 'good'
        elif cpu_temp < 75:
            temp_status = 'warning'
        else:
            temp_status = 'danger'
        
        disk = self.get_disk_usage()
        
        
        if disk['percent'] < 70:
            disk_status = 'good'
        elif disk['percent'] < 90:
            disk_status = 'warning'
        else:
            disk_status = 'danger'
        
        return {
            'cpu_temperature': cpu_temp,
            'cpu_temp_status': temp_status,
            'cpu_usage': self.get_cpu_usage(),
            'disk_usage': disk,
            'disk_status': disk_status,
            'memory_usage': self.get_memory_usage(),
            'uptime': self.get_uptime(),
            'network': self.get_network_info(),
            'timestamp': datetime.now().isoformat()
        }
    
    def _bytes_to_gb(self, bytes_value):
        """Convert bytes to GB"""
        return round(bytes_value / (1024 ** 3), 2)
    
    def _bytes_to_mb(self, bytes_value):
        """Convert bytes to MB"""
        return round(bytes_value / (1024 ** 2), 2)


if __name__ == '__main__':
    monitor = SystemMonitor()
    
    print("System Status:")
    print("=" * 50)
    
    status = monitor.get_status()
    
    print(f"\nCPU Temperature: {status['cpu_temperature']}°C ({status['cpu_temp_status']})")
    print(f"CPU Usage: {status['cpu_usage']}%")
    
    print(f"\nDisk Usage:")
    print(f"  Total: {status['disk_usage']['total']} GB")
    print(f"  Used: {status['disk_usage']['used']} GB ({status['disk_usage']['percent']}%)")
    print(f"  Free: {status['disk_usage']['free']} GB")
    print(f"  Status: {status['disk_status']}")
    
    print(f"\nMemory Usage:")
    print(f"  Total: {status['memory_usage']['total']} GB")
    print(f"  Used: {status['memory_usage']['used']} GB ({status['memory_usage']['percent']}%)")
    print(f"  Available: {status['memory_usage']['available']} GB")
    
    print(f"\nUptime: {status['uptime']['formatted']}")
    
    print(f"\nNetwork:")
    print(f"  Sent: {status['network']['bytes_sent']} MB")
    print(f"  Received: {status['network']['bytes_recv']} MB")
