import boto3
from datetime import datetime, timedelta
import pytz
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from prettytable import PrettyTable
import time

# AWS credentials and region (assuming they are configured via AWS CLI or environment variables)
ec2 = boto3.client('ec2')

# SMTP server configuration
smtp_host = 'smtp.gmail.com'
smtp_port = 587  # Use 587 or 2525 if needed
smtp_username = 'test'
smtp_password = 'test123'

# Email addresses
from_email = 'test@gmail.com'
to_email = 'test@gmail.com'

def create_ami(instance_id):
    try:
        # Define UTC and IST timezones
        utc = pytz.utc
        ist = pytz.timezone('Asia/Kolkata')
        
        # Get current time in UTC
        now_utc = datetime.utcnow()
        
        # Convert UTC time to IST
        now_ist = now_utc.astimezone(ist)
        
        # Get instance name
        response = ec2.describe_instances(InstanceIds=[instance_id])
        tags = response['Reservations'][0]['Instances'][0].get('Tags', [])
        instance_name = next((tag['Value'] for tag in tags if tag['Key'] == 'Name'), instance_id)
        
        # Create AMI name with instance name and timestamp
        ami_name = f'AMI_{instance_name}_{now_ist.strftime("%Y-%m-%d_%H-%M-%S")}'
        
        # Create the AMI
        print(f"Creating AMI for instance {instance_id} with name {ami_name}")
        response = ec2.create_image(
            InstanceId=instance_id,
            Name=ami_name,
            Description='Daily AMI Backup',
            NoReboot=True
        )

        ami_id = response['ImageId']
        print(f"Created new AMI: {ami_id}")
        
        # Tag the AMI
        creation_date = now_ist.strftime("%Y-%m-%d")
        ec2.create_tags(
            Resources=[ami_id],
            Tags=[
                {'Key': 'Name', 'Value': instance_name},
                {'Key': 'CreatedByScript', 'Value': creation_date}
            ]
        )
        
        # Wait and retry to get snapshot IDs
        snapshot_ids = []
        for _ in range(5):
            try:
                image_response = ec2.describe_images(ImageIds=[ami_id])
                for block_device in image_response['Images'][0]['BlockDeviceMappings']:
                    if 'Ebs' in block_device:
                        snapshot_id = block_device['Ebs']['SnapshotId']
                        ec2.create_tags(
                            Resources=[snapshot_id],
                            Tags=[
                                {'Key': 'Name', 'Value': instance_name},
                                {'Key': 'CreatedByScript', 'Value': creation_date}
                            ]
                        )
                        snapshot_ids.append(snapshot_id)
                if snapshot_ids:
                    break
            except KeyError:
                print("Snapshot IDs not yet available, retrying...")
                time.sleep(10)
        
        return ami_id, instance_name, snapshot_ids
    except Exception as e:
        print(f"Error in create_ami: {e}")
        return None, None, []

def delete_old_amis_and_snapshots():
    try:
        # Define UTC and IST timezones
        utc = pytz.utc
        ist = pytz.timezone('Asia/Kolkata')

        # Get current time in UTC
        now_utc = datetime.utcnow()
        
        # Calculate the date string for the previous day
        yesterday = (now_utc - timedelta(days=1)).astimezone(ist).strftime("%Y-%m-%d")
        
        # Describe all AMIs
        response = ec2.describe_images(Owners=['self'])
        deleted_amis = []
        deleted_snapshots = []
        
        for image in response['Images']:
            image_id = image['ImageId']
            tags = {tag['Key']: tag['Value'] for tag in image.get('Tags', [])}
            
            # Check if the AMI was created by this script and is from the previous day
            if tags.get('CreatedByScript') == yesterday:
                instance_name = tags.get('Name', 'Unknown')
                print(f"Deregistering AMI: {image_id}")
                ec2.deregister_image(ImageId=image_id)
                deleted_amis.append((image_id, instance_name))
                
                # Delete associated snapshots
                for block_device in image['BlockDeviceMappings']:
                    snapshot_id = block_device.get('Ebs', {}).get('SnapshotId')
                    if snapshot_id:
                        print(f"Deleting snapshot: {snapshot_id}")
                        ec2.delete_snapshot(SnapshotId=snapshot_id)
                        deleted_snapshots.append((snapshot_id, instance_name))
        
        return deleted_amis, deleted_snapshots
    except Exception as e:
        print(f"Error in delete_old_amis_and_snapshots: {e}")
        return [], []

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        print("Sending email...")
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()  # Use SSL/TLS
            server.login(smtp_username, smtp_password)
            text = msg.as_string()
            server.sendmail(from_email, to_email, text)
            print("Email sent successfully")
    except Exception as e:
        print(f"Failed to send email: {e}")

def lambda_handler(event, context):
    created_amis = []
    deleted_amis = []
    deleted_snapshots = []
    
    # List of instance IDs to create AMIs
    instance_ids = ['i-0813asdasdad212334', 'i-asda123213sdsad']
    
    # Create AMIs
    for instance_id in instance_ids:
        ami_id, instance_name, snapshot_ids = create_ami(instance_id)
        if ami_id:
            created_amis.append((ami_id, instance_name, snapshot_ids))
    
    # Delete old AMIs and snapshots
    old_amis, old_snapshots = delete_old_amis_and_snapshots()
    deleted_amis.extend(old_amis)
    deleted_snapshots.extend(old_snapshots)
    
    # Prepare email content
    created_table = PrettyTable(['S.No', 'AMI ID', 'Instance Name', 'Snapshot IDs'])
    for idx, (ami_id, instance_name, snapshot_ids) in enumerate(created_amis, 1):
        created_table.add_row([idx, ami_id, instance_name, ', '.join(snapshot_ids)])
    
    deleted_table = PrettyTable(['S.No', 'AMI ID', 'Instance Name', 'Snapshot IDs'])
    for idx, (ami_id, instance_name) in enumerate(deleted_amis, 1):
        snapshot_ids = [sid for sid, sname in deleted_snapshots if sname == instance_name]
        deleted_table.add_row([idx, ami_id, instance_name, ', '.join(snapshot_ids)])
    
    body = f"Created AMIs and Snapshots:\n\n{created_table}\n\nDeleted AMIs and Snapshots:\n\n{deleted_table}"
    subject = "Daily AMI Backup Report"
    
    # Send email
    send_email(subject, body)

# Uncomment below lines if you are running this script directly as a standalone Python script
if __name__ == "__main__":
    lambda_handler(None, None)
