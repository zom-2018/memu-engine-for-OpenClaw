"""Database migration for adding agentName to existing memories."""

import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger(__name__)

TABLES_TO_MIGRATE = [
    'memu_memory_categories',
    'memu_memory_items',
    'memu_resources',
    'memu_category_items'
]


def check_database_version(db_path: str) -> Dict[str, any]:
    """
    检查数据库版本和表状态。
    
    Args:
        db_path: 数据库文件路径
    
    Returns:
        {
            'version': 'v1' | 'v2',
            'tables_status': {table_name: has_agentName | None, ...},
            'needs_migration': bool
        }
    """
    try:
        conn = sqlite3.connect(db_path, timeout=10.0)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        
        tables_status = {}
        for table in TABLES_TO_MIGRATE:
            if table not in existing_tables:
                tables_status[table] = None
            else:
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [row[1] for row in cursor.fetchall()]
                tables_status[table] = "agentName" in columns
        
        conn.close()
        
        existing_tables_status = {
            table: has_column 
            for table, has_column in tables_status.items()
            if has_column is not None
        }
        
        if not existing_tables_status:
            version = "v2"
            needs_migration = False
        else:
            has_agentName = all(existing_tables_status.values())
            needs_migration = any(status is False for status in existing_tables_status.values())
            version = "v2" if has_agentName else "v1"
        
        logger.info(f"Database version: {version}")
        for table, status in tables_status.items():
            if status is None:
                logger.debug(f"  {table}: table not found")
            elif status:
                logger.debug(f"  {table}: has agentName column")
            else:
                logger.info(f"  {table}: missing agentName column")
        
        return {
            'version': version,
            'tables_status': tables_status,
            'needs_migration': needs_migration
        }
    except Exception as e:
        logger.error(f"Version check failed: {e}")
        return {
            'version': 'v1',
            'tables_status': {},
            'needs_migration': True
        }


def backup_database(db_path: str) -> str:
    """
    备份数据库文件。
    
    Args:
        db_path: 数据库文件路径
    
    Returns:
        备份文件路径
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{db_path}.backup_{timestamp}"
        
        shutil.copy2(db_path, backup_path)
        logger.info(f"数据库已备份到: {backup_path}")
        
        return backup_path
    except Exception as e:
        logger.error(f"Database backup failed: {e}")
        raise


def _execute_migration(db_path: str, tables_to_migrate: List[str]) -> bool:
    """
    执行实际的 ALTER TABLE 操作。
    
    Args:
        db_path: 数据库文件路径
        tables_to_migrate: 需要迁移的表列表
    
    Returns:
        True if successful, False otherwise
    """
    try:
        conn = sqlite3.connect(db_path, timeout=30.0)
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        
        migrated_tables = []
        skipped_tables = []
        failed_tables = []
        
        for table in tables_to_migrate:
            if table not in existing_tables:
                logger.info(f"  ⊘ {table} does not exist, skipping")
                skipped_tables.append(table)
                continue
            
            try:
                logger.info(f"Adding agentName column to {table}...")
                cursor.execute(f'ALTER TABLE {table} ADD COLUMN agentName TEXT DEFAULT "main"')
                migrated_tables.append(table)
                logger.info(f"  ✓ {table} migrated successfully")
            except sqlite3.OperationalError as e:
                error_msg = str(e).lower()
                if "duplicate column" in error_msg or "already exists" in error_msg:
                    logger.info(f"  ✓ {table} already has agentName column")
                    migrated_tables.append(table)
                else:
                    logger.error(f"  ✗ {table} migration failed: {e}")
                    failed_tables.append(table)
        
        conn.commit()
        conn.close()
        
        if failed_tables:
            logger.error(f"Migration completed with errors. Failed tables: {failed_tables}")
            return False
        
        if skipped_tables:
            logger.info(f"Skipped non-existent tables: {skipped_tables}")
        
        logger.info(f"Migration successful. Migrated {len(migrated_tables)} tables.")
        return True
        
    except Exception as e:
        logger.error(f"Migration execution failed: {e}")
        return False


def verify_migration(db_path: str) -> bool:
    """
    验证迁移是否成功。
    
    只验证实际存在的表,不要求所有表都存在。
    
    Args:
        db_path: 数据库文件路径
    
    Returns:
        True if all existing tables have agentName column
    """
    try:
        status = check_database_version(db_path)
        
        existing_tables_status = {
            table: has_column 
            for table, has_column in status['tables_status'].items()
            if has_column is not None
        }
        
        if not existing_tables_status:
            logger.warning("No tables found in database")
            return True
        
        all_have_column = all(existing_tables_status.values())
        
        if all_have_column:
            logger.info("Migration verification: PASSED")
            logger.info(f"  Verified {len(existing_tables_status)} table(s)")
            return True
        else:
            logger.error("Migration verification: FAILED")
            for table, has_column in existing_tables_status.items():
                if not has_column:
                    logger.error(f"  {table}: missing agentName column")
            return False
    except Exception as e:
        logger.error(f"Migration verification failed: {e}")
        return False


def migrate_existing_memories(db_path: str) -> bool:
    """
    执行数据库迁移,添加 agentName 列到所有相关表。
    
    这个函数会:
    1. 检查数据库版本
    2. 如果需要迁移,创建备份
    3. 执行 ALTER TABLE 添加 agentName 列
    4. 验证迁移结果
    
    Args:
        db_path: 数据库文件路径
    
    Returns:
        True if migration succeeded or not needed, False if failed
    """
    try:
        if not Path(db_path).exists():
            logger.info("Database does not exist yet, skipping migration")
            return True
        
        logger.info("Checking database migration status...")
        status = check_database_version(db_path)
        
        if not status['needs_migration']:
            logger.info("Database already migrated (v2), skipping")
            return True
        
        logger.info("Database migration required (v1 → v2)")
        
        tables_to_migrate = [
            table for table, has_column in status['tables_status'].items()
            if has_column is False
        ]
        
        if not tables_to_migrate:
            logger.info("No tables need migration")
            return True
        
        logger.info(f"Tables to migrate: {tables_to_migrate}")
        
        try:
            backup_path = backup_database(db_path)
            logger.info(f"Backup created: {backup_path}")
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            logger.warning("Proceeding with migration without backup (risky!)")
        
        logger.info("Executing migration...")
        success = _execute_migration(db_path, tables_to_migrate)
        
        if success:
            if verify_migration(db_path):
                logger.info("✓ Database migration completed successfully")
                return True
            else:
                logger.error("✗ Migration verification failed")
                return False
        else:
            logger.error("✗ Migration execution failed")
            return False
        
    except Exception as e:
        logger.error(f"Migration failed with exception: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False
